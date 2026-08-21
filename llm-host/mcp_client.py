import os
import sys
import json
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent


def _server_parameters(server_path: str) -> StdioServerParameters:
    """Create MCP stdio parameters with the host environment inherited.

    The MCP SDK's default child environment is intentionally limited and can
    omit application-specific variables. In Docker this caused STOCK_SERVICE_URL
    to disappear, making stock/marketplace MCP children fall back to
    http://localhost:8081 inside the llm-host container. Passing an explicit
    copy preserves normal subprocess inheritance semantics for our trusted MCP
    child processes without allowing them to mutate the parent environment.
    """
    return StdioServerParameters(
        # app.py hangi Python ile çalışıyorsa server da aynı Python ile açılır
        command=sys.executable,
        args=[server_path],
        env=dict(os.environ),
    )


class MCPClient:

    def __init__(self, servers: dict[str, str]):
        """
        Birden fazla MCP server kabul eder.

        Örnek:
        {
            "stock-server": "C:/.../stock-mcp/tools.py",
            "marketplace-server": "C:/.../marketplace-mcp/tools.py"
        }
        """
        self.servers = servers

        # Her server için ayrı session saklanır
        self.sessions: dict[str, ClientSession] = {}

        # Tool adı -> server adı eşleşmesi
        self.tool_to_server: dict[str, str] = {}

        # Tüm async context'leri güvenli kapatmak için
        self.exit_stack = AsyncExitStack()

    async def connect(self):
        """
        Bütün MCP server'larına bağlanır ve tool'ları indeksler.
        """

        for server_name, server_path in self.servers.items():
            print(f"{server_name} MCP server'ına bağlanılıyor...")

            server_params = _server_parameters(server_path)

            read, write = await self.exit_stack.enter_async_context(
                stdio_client(server_params)
            )

            session = await self.exit_stack.enter_async_context(
                ClientSession(read, write)
            )

            await session.initialize()

            self.sessions[server_name] = session

            tools_response = await session.list_tools()

            for tool in tools_response.tools:
                if not self._register_tool(tool.name, server_name):
                    continue

                print(
                    f"  Tool bulundu: {tool.name} "
                    f"({server_name})"
                )

            print(f"{server_name} bağlantısı başarılı.")

        print(
            f"Toplam {len(self.sessions)} MCP server ve "
            f"{len(self.tool_to_server)} tool bağlandı."
        )

    def _register_tool(self, tool_name: str, server_name: str) -> bool:
        """Register a tool, returning False for a duplicate from one server."""
        previous_server = self.tool_to_server.get(tool_name)
        if previous_server == server_name:
            # Some MCP SDK/server version combinations can return the same
            # tool more than once while collecting paginated responses.
            return False
        if previous_server is not None:
            raise ValueError(
                f"'{tool_name}' isimli tool hem "
                f"'{previous_server}' hem de "
                f"'{server_name}' server'ında mevcut."
            )
        self.tool_to_server[tool_name] = server_name
        return True

    async def list_tools(self):
        """
        Bütün server'lardaki tool'ları tek listede döndürür.
        """

        all_tools = []
        seen_tool_names = set()

        for session in self.sessions.values():
            tools_response = await session.list_tools()
            for tool in tools_response.tools:
                if tool.name in seen_tool_names:
                    continue
                seen_tool_names.add(tool.name)
                all_tools.append(tool)

        return all_tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict | None = None
    ):
        """
        Tool'un hangi server'da olduğunu bulur ve çalıştırır.
        """

        if arguments is None:
            arguments = {}

        server_name = self.tool_to_server.get(tool_name)

        if server_name is None:
            available_tools = ", ".join(
                sorted(self.tool_to_server.keys())
            )

            raise ValueError(
                f"'{tool_name}' adlı tool bulunamadı. "
                f"Mevcut tool'lar: {available_tools}"
            )

        session = self.sessions[server_name]

        print(
            f"[MCP] Tool çalıştırılıyor: "
            f"{server_name}.{tool_name}"
        )

        result = await session.call_tool(
            tool_name,
            arguments=arguments
        )

        return result

    async def close(self):
        """
        Bütün MCP session ve stdio bağlantılarını kapatır.
        """

        await self.exit_stack.aclose()

        self.sessions.clear()
        self.tool_to_server.clear()

        print("MCP connections closed.")
