from paw.mcp.server import build_mcp


async def test_registers_three_read_tools():
    mcp = build_mcp()
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == {"search_wiki", "get_article", "list_links"}


def test_streamable_path_is_root():
    mcp = build_mcp()
    assert mcp.settings.streamable_http_path == "/"
