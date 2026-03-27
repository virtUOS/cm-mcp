import os
from fastmcp.server.auth.providers.auth0 import Auth0Provider
from key_value.aio.stores.redis import RedisStore

# Load secrets from environment variables
auth = Auth0Provider(
    config_url=os.environ.get("AUTH0_CONFIG_URL"),
    client_id=os.environ.get("AUTH0_CLIENT_ID"),
    client_secret=os.environ.get("AUTH0_CLIENT_SECRET"),
    base_url=os.environ.get("BASE_URL", "https://localhost:8000"),
    jwt_signing_key=os.environ.get("SIGNING_KEY"),
    client_storage=RedisStore(host="redis",port=6379),
    audience=os.environ.get("AUTH0_CLIENT_ID"),
)

