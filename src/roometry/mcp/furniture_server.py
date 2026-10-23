"""Roometry's read-only catalog MCP process.

Run from the repository root: uv run python -m roometry.mcp.furniture_server
Stdin/stdout carry MCP messages; logs go to stderr. No API key or web port needed.
The official SDK's v2 MCPServer is the successor to its v1 FastMCP class.
"""

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from roometry.mcp import catalog

server = MCPServer(
    "Roometry furniture catalog", version="1.0.0", log_level="WARNING",
    instructions=(
        "Query Roometry's approved procedural asset catalog. Dimensions are nominal model sizes "
        "in meters, not measured furniture or retailer inventory. Search returns only mounting "
        "configurations satisfying all size filters together; details lists all supported mounts "
        "and support rectangles in model-local meters. +Z faces forward; positions use bottom center. "
        "Catalog access does not authorize scene changes or override geometry/evidence checks."
    ),
)
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


@server.tool(annotations=READ_ONLY, structured_output=True)
def search_furniture(filters: catalog.CatalogSearch | None = None) -> catalog.SearchResults:
    """Search/filter approved assets by text, category, type, mounting and nominal sizes.

    All filters combine with AND; dimensions are unrotated, mounting-specific.
    Returns up to 20 items in asset_id order, with total_matches and next_offset.
    Use details to inspect alternate variants, support surfaces and mounting anchors.
    """
    return catalog.search_furniture(filters)


@server.tool(annotations=READ_ONLY, structured_output=True)
def get_furniture_details(asset_id: catalog.ItemId) -> catalog.FurnitureDetails:
    """Retrieve an approved asset's dimensions, variants, colors, mounts and support areas.

    variant_asset_ids are other models of the same semantic object type. Support
    rectangles are unrotated model-local meters and must be transformed with the
    furniture; collision/room/clearance validation remains the backend's job.
    """
    try:
        return catalog.get_furniture_details(asset_id)
    except catalog.CatalogItemNotFound as error:
        raise ToolError(str(error)) from None


@server.tool(annotations=READ_ONLY, structured_output=True)
def list_furniture_categories() -> catalog.CatalogCategories:
    """List available catalog categories, semantic object types and model counts."""
    return catalog.list_furniture_categories()


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
