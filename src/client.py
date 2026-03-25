"""MCP test client using aiohttp for OpenAI API."""

import asyncio
import json
import os

import aiohttp
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()

# Config
API_KEY = os.getenv("AI_GATEWAY_KEY")
BASE_URL = os.getenv("AI_GATEWAY_BASE_URL", "https://api.openai.com/v1")
MODEL = os.getenv("MODEL", "gpt-oss")


async def chat_completion(session: aiohttp.ClientSession, messages: list, tools: list) -> dict:
    """Call OpenAI-compatible chat completion endpoint."""
    url = f"{BASE_URL}/chat/completions"
    
    payload = {
        "model": MODEL,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    
    async with session.post(url, json=payload, headers=headers) as resp:
        if resp.status != 200:
            error = await resp.text()
            raise Exception(f"API error {resp.status}: {error}")
        return await resp.json()


async def main():
    server_params = StdioServerParameters(
        command="python",
        args=["/his-mcp/src/server.py"],
        env={**os.environ},
    )
    
    async with aiohttp.ClientSession() as http:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as mcp:
                await mcp.initialize()
                
                # Get tools in OpenAI format
                mcp_tools = await mcp.list_tools()
                tools = [
                    {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.inputSchema or {"type": "object", "properties": {}},
                        },
                    }
                    for t in mcp_tools.tools
                ]
                
                print(f"Connected with {len(tools)} tools")
                print(f"Using: {BASE_URL} / {MODEL}")
                
                messages = [
    {
        "role": "system", 
        "content": """You are a database analyst. Help users understand their data.

Workflow:
1. Explore schema first (list_tables, describe_table)
2. Understand data format (sample_data, column_values)  
3. Run queries to get data
4. **Interpret results and provide a clear answer**

Important:
- Don't just show raw data - explain what it means
- Answer the user's actual question
- Mention which tables you used
- If no results, explain why and suggest alternatives

Format your answers as:
- **Answer**: Direct response to the question
- **Details**: Supporting data if relevant
- **Source**: Tables/columns used"""
    },
]
                
                while True:
                    user_input = input("\nYou: ").strip()
                    if user_input.lower() in ["quit", "exit", "/quit"]:
                        break
                    
                    if not user_input:
                        continue
                    
                    messages.append({"role": "user", "content": user_input})
                    
                    # Inner loop for tool calls
                    while True:
                        response = await chat_completion(http, messages, tools)
                        msg = response["choices"][0]["message"]
                        
                        tool_calls = msg.get("tool_calls")
                        content = msg.get("content") or ""  # <-- Fix: handle None
                        
                        if not tool_calls:
                            # No tools, final answer
                            print(f"\nAssistant: {content}")
                            messages.append({"role": "assistant", "content": content})
                            break
                        
                        # Has tool calls
                        messages.append({
                            "role": "assistant",
                            "content": content,
                            "tool_calls": tool_calls,
                        })
                        
                        for tc in tool_calls:
                            func = tc["function"]
                            args = json.loads(func["arguments"]) if func["arguments"] else {}
                            print(f"\n🔧 {func['name']}({args})")
                            
                            result = await mcp.call_tool(func["name"], args)
                            result_text = result.content[0].text if result.content else ""
                            
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": result_text,
                            })


if __name__ == "__main__":
    asyncio.run(main())