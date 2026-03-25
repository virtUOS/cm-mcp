"""PostgreSQL MCP Server with curated discovery but unrestricted queries.

Uses FastMCP for clean tool definitions and separates data fetching
from formatting for better maintainability.
"""

import json
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path
from typing import Any
from starlette.responses import JSONResponse
from fastmcp.server.event_store import EventStore

async def health_check(request):
    return JSONResponse({"status": "healthy", "service": "mcp-server"})
import asyncpg
import yaml
from dotenv import load_dotenv
from fastmcp import FastMCP
from pydantic import BaseModel, Field

load_dotenv()


# ============ CONFIGURATION ============
"""
The whitelist (table) acts as a "starting point" or "curated entry points" into the database, 
but once the LLM is exploring via SQL, it shouldn't be blocked from joining to related tables it 
discovers through foreign keys.
"""

class TableConfig(BaseModel):
    """Configuration for a curated table."""
    name: str
    description: str = ""
    key_columns: dict[str, str] = Field(default_factory=dict)


class Config(BaseModel):
    """Main configuration."""
    database_url: str
    curated_tables: dict[str, TableConfig]
    max_query_rows: int = 100
    sample_rows: int = 5

    @classmethod
    def load(cls, config_path: Path | None = None) -> "Config":
        database_url = os.getenv("DATABASE_URL") # Provided in docker compose
        if not database_url:
            raise ValueError("DATABASE_URL environment variable required")

        if config_path is None:
            config_path = Path(__file__).parent / "config" / "allowed_tables.yaml"

        curated_tables = {}
        if config_path.exists():
            with open(config_path) as f:
                yaml_config = yaml.safe_load(f)

            for table_data in yaml_config.get("tables", []):
                if isinstance(table_data, str):
                    curated_tables[table_data.lower()] = TableConfig(name=table_data)
                else:
                    name = table_data["name"]
                    curated_tables[name.lower()] = TableConfig(
                        name=name,
                        description=table_data.get("description", ""),
                        key_columns=table_data.get("key_columns", {}),
                    )

        return cls(database_url=database_url, curated_tables=curated_tables)

    def is_curated(self, table: str) -> bool:
        """Check if table is in the curated list."""
        return table.lower() in self.curated_tables

    def get_curated_names(self) -> list[str]:
        """Get list of curated table names."""
        return [t.name for t in self.curated_tables.values()]

    def get_table_config(self, table: str) -> TableConfig | None:
        """Get config for a curated table."""
        return self.curated_tables.get(table.lower())


# ============ DATA CLASSES ============

@dataclass
class ColumnInfo:
    """Information about a table column."""
    name: str
    data_type: str
    is_nullable: bool
    default: str | None
    is_primary_key: bool = False
    hint: str = ""


@dataclass
class ForeignKeyInfo:
    """Information about a foreign key relationship."""
    column: str
    foreign_table: str
    foreign_column: str


@dataclass
class TableSchema:
    """Complete schema information for a table."""
    name: str
    row_count: int
    columns: list[ColumnInfo]
    foreign_keys: list[ForeignKeyInfo]
    description: str = ""
    is_curated: bool = False


@dataclass
class ColumnValues:
    """Distinct values for a column."""
    table_name: str
    column_name: str
    values: list[tuple[Any, int]]  # (value, count)


@dataclass
class RelatedTables:
    """Tables related via foreign keys."""
    table_name: str
    outgoing: list[dict[str, str]]  # FKs from this table
    incoming: list[dict[str, str]]  # FKs pointing to this table


@dataclass 
class TableInfo:
    """Basic table information."""
    name: str
    row_count: int
    description: str = ""


@dataclass
class SchemaSearchResult:
    """Search results for schema search."""
    search_term: str
    matching_tables: list[TableInfo]
    matching_columns: list[dict[str, str]]


# ============ DATABASE ============

class Database:
    """PostgreSQL connection manager with schema introspection."""
    
    SCHEMA = "hisinone"

    def __init__(self, config: Config):
        self.config = config
        self.pool: asyncpg.Pool | None = None
        self._all_tables: set[str] | None = None

    async def connect(self):
        """Create connection pool and cache table names."""
        self.pool = await asyncpg.create_pool(
            host=os.getenv("POSTGRES_HOST"),
            port=5432,
            database=os.getenv("POSTGRES_DB"),
            user=os.getenv("POSTGRES_USER"),
            password=os.getenv("POSTGRES_PASSWORD"),
            min_size=2,
            max_size=10,
            server_settings={"search_path": self.SCHEMA},
        )
        
        async with self.acquire() as conn:
            rows = await conn.fetch("""
                SELECT table_name 
                FROM information_schema.tables
                WHERE table_schema = $1
            """, self.SCHEMA)
            self._all_tables = {row["table_name"].lower() for row in rows}
            print(f"✓ Cached {len(self._all_tables)} tables from '{self.SCHEMA}' schema")

    async def disconnect(self):
        """Close connection pool."""
        if self.pool:
            await self.pool.close()

    @asynccontextmanager
    async def acquire(self):
        """Acquire a connection from the pool."""
        if not self.pool:
            await self.connect()
        async with self.pool.acquire() as conn:
            yield conn

    def table_exists(self, table: str) -> bool:
        """Check if table exists in database."""
        if self._all_tables is None:
            return True
        return table.lower() in self._all_tables

    def validate_curated_table(self, table: str) -> str:
        """Validate table is in curated list."""
        clean = table.split(".")[-1].strip('"').lower()
        if not self.config.is_curated(clean):
            raise ValueError(
                f"Table '{table}' is not in the curated list. "
                f"Use 'list_tables' to see available tables, or "
                f"'explore_table' for tables discovered via foreign keys."
            )
        return clean

    def validate_any_table(self, table: str) -> str:
        """Validate table exists in database."""
        clean = table.split(".")[-1].strip('"').lower()
        if not self.table_exists(clean):
            raise ValueError(f"Table '{table}' does not exist in the database.")
        return clean

    def validate_query(self, sql: str) -> str:
        """Validate query and add schema prefix to tables."""
        sql = sql.strip().rstrip(';')
        
        if not sql.upper().startswith("SELECT"):
            raise ValueError("Only SELECT queries are allowed")
        
        dangerous = [
            r'\bINTO\s+', r'\bDROP\b', r'\bDELETE\b', r'\bUPDATE\b',
            r'\bINSERT\b', r'\bTRUNCATE\b', r'\bALTER\b', r'\bCREATE\b',
            r'\bGRANT\b', r'\bREVOKE\b', r';\s*\w',
        ]
        for pattern in dangerous:
            if re.search(pattern, sql, re.IGNORECASE):
                raise ValueError("Query contains disallowed operations")
        
        # Add schema prefix to table names
        def add_schema(match):
            keyword = match.group(1)
            table = match.group(2).strip('"')
            if '.' in table:
                return match.group(0)
            return f'{keyword} {self.SCHEMA}."{table}"'
        
        sql = re.sub(
            r'\b(FROM|JOIN)\s+(["\w]+)',
            add_schema,
            sql,
            flags=re.IGNORECASE
        )
        
        return sql

    async def get_tables_info(self, table_names: list[str]) -> list[TableInfo]:
        """Get basic info for a list of tables."""
        async with self.acquire() as conn:
            rows = await conn.fetch("""
                SELECT t.table_name, pg_stat_get_live_tuples(c.oid) as row_count
                FROM information_schema.tables t
                JOIN pg_class c ON c.relname = t.table_name
                JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = $1
                WHERE t.table_schema = $1
                  AND t.table_name = ANY($2)
                ORDER BY t.table_name
            """, self.SCHEMA, table_names)

            result = []
            for row in rows:
                table_cfg = self.config.get_table_config(row["table_name"])
                result.append(TableInfo(
                    name=row["table_name"],
                    row_count=row["row_count"] or 0,
                    description=table_cfg.description if table_cfg else "",
                ))
            return result

    async def get_table_schema(self, table_name: str) -> TableSchema:
        """Fetch complete schema information for a table."""
        async with self.acquire() as conn:
            columns_raw = await conn.fetch("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = $2
                ORDER BY ordinal_position
            """, self.SCHEMA, table_name)

            if not columns_raw:
                raise ValueError(f"Table '{table_name}' not found or has no columns")

            # Get primary keys
            try:
                pks = await conn.fetch(f"""
                    SELECT a.attname
                    FROM pg_index i
                    JOIN pg_attribute a ON a.attrelid = i.indrelid 
                        AND a.attnum = ANY(i.indkey)
                    WHERE i.indrelid = '{self.SCHEMA}."{table_name}"'::regclass 
                      AND i.indisprimary
                """)
                pk_cols = {r["attname"] for r in pks}
            except Exception:
                pk_cols = set()

            # Get foreign keys
            fks_raw = await conn.fetch("""
                SELECT 
                    kcu.column_name, 
                    ccu.table_name AS foreign_table, 
                    ccu.column_name AS foreign_column
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu 
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu 
                    ON ccu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY' 
                  AND tc.table_schema = $1
                  AND tc.table_name = $2
            """, self.SCHEMA, table_name)

            # Get row count
            row_count = await conn.fetchval(
                f'SELECT COUNT(*) FROM {self.SCHEMA}."{table_name}"'
            )

            # Get table config
            table_cfg = self.config.get_table_config(table_name)

            # Build column info
            columns = []
            for col in columns_raw:
                hint = ""
                if table_cfg and col["column_name"] in table_cfg.key_columns:
                    hint = table_cfg.key_columns[col["column_name"]]
                
                columns.append(ColumnInfo(
                    name=col["column_name"],
                    data_type=col["data_type"],
                    is_nullable=col["is_nullable"] == "YES",
                    default=col["column_default"],
                    is_primary_key=col["column_name"] in pk_cols,
                    hint=hint,
                ))

            # Build foreign key info
            foreign_keys = [
                ForeignKeyInfo(
                    column=fk["column_name"],
                    foreign_table=fk["foreign_table"],
                    foreign_column=fk["foreign_column"],
                )
                for fk in fks_raw
            ]

            return TableSchema(
                name=table_name,
                row_count=row_count or 0,
                columns=columns,
                foreign_keys=foreign_keys,
                description=table_cfg.description if table_cfg else "",
                is_curated=self.config.is_curated(table_name),
            )

    async def get_sample_data(self, table_name: str, limit: int = 5) -> list[dict]:
        """Get sample rows from a table."""
        limit = min(limit, 10)
        async with self.acquire() as conn:
            rows = await conn.fetch(
                f'SELECT * FROM {self.SCHEMA}."{table_name}" LIMIT {limit}'
            )
            return [dict(row) for row in rows]

    async def get_column_values(
        self, 
        table_name: str, 
        column_name: str,
        limit: int = 25,
    ) -> ColumnValues:
        """Get distinct values for a column with counts."""
        async with self.acquire() as conn:
            exists = await conn.fetchval("""
                SELECT EXISTS(
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = $1 
                      AND table_name = $2 
                      AND column_name = $3
                )
            """, self.SCHEMA, table_name, column_name)

            if not exists:
                raise ValueError(f"Column '{column_name}' not found in '{table_name}'")

            rows = await conn.fetch(f"""
                SELECT "{column_name}" as value, COUNT(*) as count
                FROM {self.SCHEMA}."{table_name}"
                GROUP BY "{column_name}"
                ORDER BY count DESC
                LIMIT {limit}
            """)

            return ColumnValues(
                table_name=table_name,
                column_name=column_name,
                values=[(row["value"], row["count"]) for row in rows],
            )

    async def get_related_tables(self, table_name: str) -> RelatedTables:
        """Find all tables related via foreign keys."""
        async with self.acquire() as conn:
            outgoing = await conn.fetch("""
                SELECT
                    kcu.column_name as from_column,
                    ccu.table_name as to_table,
                    ccu.column_name as to_column
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu 
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu 
                    ON ccu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = $1
                  AND tc.table_name = $2
            """, self.SCHEMA, table_name)

            incoming = await conn.fetch("""
                SELECT
                    tc.table_name as from_table,
                    kcu.column_name as from_column,
                    ccu.column_name as to_column
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu 
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu 
                    ON ccu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = $1
                  AND ccu.table_name = $2
            """, self.SCHEMA, table_name)

            return RelatedTables(
                table_name=table_name,
                outgoing=[dict(row) for row in outgoing],
                incoming=[dict(row) for row in incoming],
            )

    async def search_schema(self, search_term: str) -> SchemaSearchResult:
        """Search for tables and columns matching a term."""
        term = search_term.lower()
        curated = self.config.get_curated_names()

        matching_table_names = [t for t in curated if term in t.lower()]
        matching_tables = await self.get_tables_info(matching_table_names)

        async with self.acquire() as conn:
            columns = await conn.fetch("""
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = $1
                  AND table_name = ANY($2)
                  AND lower(column_name) LIKE $3
                ORDER BY table_name, column_name
            """, self.SCHEMA, curated, f"%{term}%")

            return SchemaSearchResult(
                search_term=search_term,
                matching_tables=matching_tables,
                matching_columns=[dict(col) for col in columns],
            )

    async def execute_query(self, sql: str) -> list[dict]:
        """Execute a validated SELECT query."""
        sql = self.validate_query(sql)
        
        async with self.acquire() as conn:
            rows = await conn.fetch(sql)
            return [dict(row) for row in rows]
# ============ FORMATTERS ============

class Formatter:
    """Formats data objects as markdown strings."""

    def __init__(self, config: Config):
        self.config = config

    def format_tables_list(self, tables: list[TableInfo]) -> str:
        """Format list of tables as markdown."""
        lines = [
            f"# Main Tables ({len(tables)} curated)\n",
            "_These are the primary tables. Related tables can be explored via foreign keys._\n",
        ]

        for table in tables:
            desc = f" — {table.description}" if table.description else ""
            lines.append(f"- **{table.name}** ({table.row_count:,} rows){desc}")

        return "\n".join(lines)

    def format_table_schema(self, schema: TableSchema) -> str:
        """Format table schema as markdown."""
        status = "⭐ Curated" if schema.is_curated else "📎 Related table (not curated)"

        lines = [
            f"# {schema.name}",
            f"_{status}_",
        ]
        
        if schema.description:
            lines.append(f"_{schema.description}_")
        
        lines.extend([
            "",
            f"**Rows:** {schema.row_count:,}\n",
            "## Columns\n",
        ])

        for col in schema.columns:
            pk = " 🔑" if col.is_primary_key else ""
            nullable = " (nullable)" if col.is_nullable else ""
            hint = f" — {col.hint}" if col.hint else ""
            lines.append(f"- **{col.name}**: `{col.data_type}`{pk}{nullable}{hint}")

        if schema.foreign_keys:
            lines.append("\n## Foreign Keys\n")
            for fk in schema.foreign_keys:
                marker = "⭐" if self.config.is_curated(fk.foreign_table) else "📎"
                lines.append(
                    f"- {fk.column} → {fk.foreign_table}.{fk.foreign_column} {marker}"
                )
            lines.append("\n_⭐ = curated, 📎 = use `explore_table` to see details_")

        return "\n".join(lines)

    def format_sample_data(self, table_name: str, data: list[dict]) -> str:
        """Format sample data as markdown."""
        if not data:
            return "Table is empty."

        formatted = json.dumps(data, indent=2, default=self._json_serializer)
        return f"## Sample from {table_name}\n\n```json\n{formatted}\n```"

    def format_column_values(self, values: ColumnValues) -> str:
        """Format column values as markdown."""
        lines = [f"## Values in {values.table_name}.{values.column_name}\n"]
        
        for value, count in values.values:
            lines.append(f"- `{value}`: {count:,}")

        return "\n".join(lines)

    def format_related_tables(self, related: RelatedTables) -> str:
        """Format related tables as markdown."""
        is_curated = self.config.is_curated(related.table_name)
        
        lines = [
            f"# Relationships for {related.table_name}\n",
            f"_This table is {'curated ⭐' if is_curated else 'not in curated list 📎'}_\n",
        ]

        if related.outgoing:
            lines.append("## References (this table points to)")
            for fk in related.outgoing:
                marker = " ⭐" if self.config.is_curated(fk["to_table"]) else ""
                lines.append(
                    f"- `{related.table_name}.{fk['from_column']}` → "
                    f"`{fk['to_table']}.{fk['to_column']}`{marker}"
                )

        if related.incoming:
            lines.append("\n## Referenced by (other tables point here)")
            for fk in related.incoming:
                marker = " ⭐" if self.config.is_curated(fk["from_table"]) else ""
                lines.append(
                    f"- `{fk['from_table']}.{fk['from_column']}` → "
                    f"`{related.table_name}.{fk['to_column']}`{marker}"
                )

        if not related.outgoing and not related.incoming:
            lines.append("No foreign key relationships found.")

        lines.append("\n_⭐ = curated table_")

        return "\n".join(lines)

    def format_search_results(self, results: SchemaSearchResult) -> str:
        """Format schema search results as markdown."""
        lines = [f"## Search: '{results.search_term}' (in curated tables)\n"]

        if results.matching_tables:
            lines.append("### Tables")
            for table in results.matching_tables:
                desc = f" — {table.description}" if table.description else ""
                lines.append(f"- {table.name}{desc}")

        if results.matching_columns:
            lines.append("\n### Columns")
            for col in results.matching_columns:
                lines.append(
                    f"- {col['table_name']}.{col['column_name']} (`{col['data_type']}`)"
                )

        if not results.matching_tables and not results.matching_columns:
            lines.append("No matches found in curated tables.")

        return "\n".join(lines)

    def format_query_results(
        self, 
        data: list[dict], 
        reasoning: str = "",
        max_rows: int = 100,
    ) -> str:
        """Format query results as markdown."""
        if not data:
            return "Query returned no results."

        truncated = len(data) > max_rows
        data = data[:max_rows]

        # Use table format for small results
        if len(data) <= 20 and len(data[0]) <= 8:
            result = self._format_as_table(data, reasoning)
        else:
            formatted = json.dumps(data, indent=2, default=self._json_serializer)
            result = f"_{reasoning}_\n\n```json\n{formatted}\n```" if reasoning else f"```json\n{formatted}\n```"

        if truncated:
            result += f"\n\n*Results truncated to {max_rows} rows*"

        return result

    def _format_as_table(self, data: list[dict], reasoning: str = "") -> str:
        """Format data as markdown table."""
        if not data:
            return "No data"

        headers = list(data[0].keys())
        lines = []

        if reasoning:
            lines.append(f"_{reasoning}_\n")

        lines.append("| " + " | ".join(str(h) for h in headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

        for row in data:
            values = []
            for v in row.values():
                str_val = str(v) if v is not None else "NULL"
                # Truncate long values and escape pipes
                str_val = str_val[:50].replace("|", "\\|")
                values.append(str_val)
            lines.append("| " + " | ".join(values) + " |")

        return "\n".join(lines)

    @staticmethod
    def _json_serializer(obj: Any) -> Any:
        """Custom JSON serializer for database types."""
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, bytes):
            return obj.hex()
        raise TypeError(f"Cannot serialize {type(obj)}")


# ============ FASTMCP SERVER ============

config = Config.load()
db = Database(config)
fmt = Formatter(config)

mcp = FastMCP("PostgreSQL Explorer")


# @mcp.on_event("startup")
# async def startup():
#     await db.connect()


# @mcp.on_event("shutdown")
# async def shutdown():
#     await db.disconnect()


# ============ CURATED DISCOVERY TOOLS ============

@mcp.tool()
async def list_tables() -> str:
    """
    List the curated set of main database tables.
    These are the primary tables you should start exploring.
    Use 'explore_table' if you discover related tables via foreign keys.
    """
    tables = await db.get_tables_info(config.get_curated_names())
    return fmt.format_tables_list(tables)


@mcp.tool()
async def describe_table(table_name: str) -> str:
    """
    Get detailed schema for a curated table.
    Shows columns, types, and foreign keys to related tables.

    For tables discovered via foreign keys, use 'explore_table' instead.

    Args:
        table_name: Name of the curated table to describe
    """
    validated_name = db.validate_curated_table(table_name)
    schema = await db.get_table_schema(validated_name)
    return fmt.format_table_schema(schema)


@mcp.tool()
async def sample_data(table_name: str, limit: int = 5) -> str:
    """
    Get sample rows from a curated table.

    Args:
        table_name: Name of the curated table
        limit: Number of rows (max 10)
    """
    validated_name = db.validate_curated_table(table_name)
    data = await db.get_sample_data(validated_name, limit)
    return fmt.format_sample_data(validated_name, data)


@mcp.tool()
async def column_values(table_name: str, column_name: str) -> str:
    """
    Get distinct values for a column in a curated table.
    Useful for understanding enums, statuses, or categories.

    Args:
        table_name: Name of the curated table
        column_name: Name of the column
    """
    validated_name = db.validate_curated_table(table_name)
    values = await db.get_column_values(validated_name, column_name)
    return fmt.format_column_values(values)


@mcp.tool()
async def search_schema(search_term: str) -> str:
    """
    Search for tables and columns within the curated tables.
    Use this to find relevant tables when unsure of exact names.

    Args:
        search_term: Keyword to search for (e.g., 'customer', 'order')
    """
    results = await db.search_schema(search_term)
    return fmt.format_search_results(results)


# ============ UNRESTRICTED EXPLORATION TOOLS ============

@mcp.tool()
async def explore_table(table_name: str) -> str:
    """
    Explore ANY table in the database, including those discovered via foreign keys.
    Use this when you find a related table that's not in the curated list.

    Args:
        table_name: Name of any table in the database
    """
    validated_name = db.validate_any_table(table_name)
    schema = await db.get_table_schema(validated_name)
    return fmt.format_table_schema(schema)


@mcp.tool()
async def explore_column_values(table_name: str, column_name: str) -> str:
    """
    Get distinct values for a column in ANY table.
    Use this for tables discovered via foreign keys.

    Args:
        table_name: Name of any table in the database
        column_name: Name of the column
    """
    validated_name = db.validate_any_table(table_name)
    values = await db.get_column_values(validated_name, column_name)
    return fmt.format_column_values(values)


@mcp.tool()
async def explore_sample_data(table_name: str, limit: int = 5) -> str:
    """
    Get sample rows from ANY table in the database.
    Use this for tables discovered via foreign keys.

    Args:
        table_name: Name of any table in the database
        limit: Number of rows (max 10)
    """
    validated_name = db.validate_any_table(table_name)
    data = await db.get_sample_data(validated_name, limit)
    return fmt.format_sample_data(validated_name, data)


@mcp.tool()
async def find_related_tables(table_name: str) -> str:
    """
    Find all tables that reference or are referenced by a given table.
    Useful for understanding the data model around a table.

    Args:
        table_name: Name of any table in the database
    """
    validated_name = db.validate_any_table(table_name)
    related = await db.get_related_tables(validated_name)
    return fmt.format_related_tables(related)


# ============ QUERY EXECUTION ============

@mcp.tool()
async def run_query(query: str, reasoning: str = "") -> str:
    """
    Execute a SELECT query against the database.
    Can query ANY table, including JOINs across curated and non-curated tables.

    Only use AFTER exploring the relevant tables to understand their schema.

    Args:
        query: The SELECT SQL query to execute
        reasoning: Brief explanation of what this query does
    """
    try:
        data = await db.execute_query(query)
        return fmt.format_query_results(data, reasoning, config.max_query_rows)
    except asyncpg.PostgresError as e:
        return f"❌ Query error: {e}"
    except ValueError as e:
        return f"❌ Validation error: {e}"


# ============ RESOURCES ============

@mcp.resource("postgres://schema/overview")
async def schema_overview() -> str:
    """Complete overview of all curated tables."""
    tables = await db.get_tables_info(config.get_curated_names())
    
    data = {
        "total_curated_tables": len(tables),
        "tables": [
            {
                "name": t.name,
                "rows": t.row_count,
                "description": t.description,
            }
            for t in tables
        ],
    }
    return json.dumps(data, indent=2)


@mcp.resource("postgres://schema/table/{table_name}")
async def table_schema_resource(table_name: str) -> str:
    """Schema for a specific table as JSON."""
    validated_name = db.validate_any_table(table_name)
    schema = await db.get_table_schema(validated_name)
    
    data = {
        "name": schema.name,
        "row_count": schema.row_count,
        "is_curated": schema.is_curated,
        "description": schema.description,
        "columns": [
            {
                "name": c.name,
                "type": c.data_type,
                "nullable": c.is_nullable,
                "primary_key": c.is_primary_key,
                "hint": c.hint,
            }
            for c in schema.columns
        ],
        "foreign_keys": [
            {
                "column": fk.column,
                "references": f"{fk.foreign_table}.{fk.foreign_column}",
            }
            for fk in schema.foreign_keys
        ],
    }
    return json.dumps(data, indent=2)




@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    return JSONResponse({"status": "healthy", "service": "mcp-server"})

# ============ ENTRY POINT ============

# Configure with EventStore for resumability
event_store = EventStore()

# Create ASGI application
app = mcp.http_app(
    event_store=event_store,
    retry_interval=2000, ) # Client reconnects after 2 seconds

if __name__ == "__main__":
    import uvicorn
    # server is accessible at the same URL: http://localhost:8000/mcp
    uvicorn.run("server:app",  host="0.0.0.0", port=8000, log_level="info")