import asyncio
import os

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

load_dotenv()
key = os.getenv("TAVILY_API_KEY")
print(f"[1] TAVILY_API_KEY cargada: {'SI' if key else 'NO'}")

URL = f"https://mcp.tavily.com/mcp/?tavilyApiKey={key}"


async def main():
    print("[2] Conectando al servidor MCP de Tavily...")
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("[3] Sesion inicializada OK")

            tools = (await session.list_tools()).tools
            print(f"[4] Herramientas disponibles: {len(tools)}")
            for t in tools:
                print(f"    - {t.name}")

            search = next(t.name for t in tools if "search" in t.name)
            print(f"[5] Probando '{search}'...")
            result = await session.call_tool(
                search, {"query": "vacuum gripper 24V UR5 datasheet"}
            )
            print("[6] Respuesta (primeros 500 caracteres):")
            print(result.content[0].text[:500])


asyncio.run(main())