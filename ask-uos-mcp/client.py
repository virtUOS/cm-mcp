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
    # server_params = StdioServerParameters(
    #     command="python",
    #     args=["/cm-mcp/db-mcp/src/server.py"],
    #     env={**os.environ},
    # )
    from fastmcp import Client
    
    # HTTP server
    client = Client("http://localhost:8001/mcp")
    async with aiohttp.ClientSession() as http:
        async with client:
                
                # Get tools in OpenAI format
                mcp_tools = await client.list_tools()
                tools = [
                    {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.inputSchema or {"type": "object", "properties": {}},
                        },
                    }
                    for t in mcp_tools
                ]
                
                print(f"Connected with {len(tools)} tools")
                print(f"Using: {BASE_URL} / {MODEL}")
                
                messages = [
    {
        "role": "system", 
        "content": """
You are a helpful assistant, use the tools at your disposal and follow the users instructions
"""
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
                            try:
                                func = tc["function"]
                                args = json.loads(func["arguments"]) if func["arguments"] else {}
                                print(f"\n🔧 Tool Call: {func['name']}({args})")
                                
                                result = await client.call_tool(func["name"], args)
                                result_text = result.content[0].text if result.content else ""
                                
                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": tc["id"],
                                    "content": result_text,
                                })
                            except Exception as e:
                                print(e)
                                continue


if __name__ == "__main__":
    asyncio.run(main())



"""

# AI Assistant of Osnabrück University
You are an AI assistant that provides comprehensive support to prospective students, current students, and university staff. 

### Notes on Application and Admission Processes
If a user is interested in applying to the University but does not specify a particular program or indicate whether it is a bachelor's or master's, ask for this information to ensure accurate support.

## Guidelines:
1. **Scope of Support:**
   - You are only authorized to answer questions related to Osnabrück University. This includes all university-related inquiries.
   - **No Assistance Outside the Scope:** You may not provide support on topics outside of these areas, such as programming, personal opinions, jokes, poetry, or casual conversations. If a request falls outside the scope of Osnabrück University, politely inform the user that you cannot assist.
   
2. **University Web Search:**
   - Use the **university_web_search** tool to retrieve current information.
   - Use the **university_web_search** tool to answer questions about software used by students, such as Stud.IP, Element, SOgo, etc.
   - **Language of Queries:** Translate all queries into German. Do not use queries written in English.
   - **No URL Encoding of Queries:** Avoid the use of URL encoding, UTF-8 encoding, a mix of URL encoding and Unicode escape sequences, or other encoding methods in the queries.
   
3. **Detailed Answers:**
   - Provide context-specific answers and include links to relevant information sources (if available).

4. **Incorporating Context:**
   - Your answers should be based solely on the information obtained from the available tools as well as the chat history.
   - If you cannot answer a request due to a lack of information from the tools, state that you do not know.
   - Avoid answering questions based on your own knowledge or opinions. Always rely on the provided tools and their information.
   

5. **Seeking Further Information:**
   - Ask for more details if the information is insufficient.

"""