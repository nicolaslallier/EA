"""The MCP adapter: the architecture graph, offered to an agent as tools.

This is a *sibling* of `api/`, not a client of it. Both occupy the same place
in the arrow `api -> services -> domain <- repositories`: they translate one
caller's request into one `ArchitectureService` call and translate the answer
back. An MCP server that spoke HTTP to our own REST API instead would put a
second copy of every payload shape in the tree and add a hop that can only
fail; worse, it would invite the next tool to reach past the service. Every
rule an agent must obey — the ArchiMate matrix, the containment loop, and the
permission checks when auth lands — is enforced below this line.

See `docs/adr/0014`.
"""

from ea.mcp.server import MCP_PATH, build_mcp_server

__all__ = ["MCP_PATH", "build_mcp_server"]
