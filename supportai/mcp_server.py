"""Read-only MCP interface to domain-specific SupportAI knowledge."""

from mcp.server import MCPServer

from supportai.domains import DOMAINS, get_domain
from supportai.knowledge import search_articles


mcp = MCPServer("SupportAI Knowledge")


@mcp.tool()
def list_support_domains() -> list[dict]:
    """List the domains that have support knowledge available."""
    return [
        {"id": domain_id, "name": config["name"], "description": config["description"]}
        for domain_id, config in DOMAINS.items()
    ]


@mcp.tool()
def get_domain_support_policies(domain: str) -> list[str]:
    """Return the configured support-policy summary for one domain."""
    return list(get_domain(domain)["policies"])


@mcp.tool()
def search_support_knowledge(query: str, domain: str, category: str = "", limit: int = 5) -> list[dict]:
    """Search support articles within one required business domain."""
    get_domain(domain)
    if limit < 1 or limit > 10:
        raise ValueError("limit must be between 1 and 10.")
    results = search_articles(
        query,
        domain=domain,
        category=category or None,
        limit=limit,
    )
    return [
        {
            "title": item.title,
            "domain": domain,
            "category": item.category,
            "content": item.content,
            "source": item.source,
            "score": item.score,
        }
        for item in results
    ]


if __name__ == "__main__":
    mcp.run(transport="stdio")
