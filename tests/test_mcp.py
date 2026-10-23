"""Catalog contracts, actual MCP discovery/calls and stdio; no LLM or API key."""

import json
import sys
from copy import deepcopy

import anyio
import pytest
from jsonschema import Draft202012Validator
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from pydantic import ValidationError

from roometry.mcp.catalog import (
    CatalogCategories, CatalogItemNotFound, CatalogSearch, FurnitureDetails,
    SearchResults, get_furniture_details, list_furniture_categories, search_furniture,
)
from roometry.mcp.furniture_server import server
from roometry.scenes.assets import ASSET_REGISTRY
from roometry.scenes.catalog import ASSET_CATALOG, dimensions_for, object_surfaces


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    async with Client(server, raise_exceptions=True) as connected:
        yield connected


def decode(model, result):
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return model.model_validate_json(json.dumps(result.structured_content))


def test_pages_cover_each_real_asset_exactly_once_in_stable_order():
    items, offset = [], 0
    while True:
        page = search_furniture(CatalogSearch(limit=7, offset=offset))
        assert len(page.items) <= 7 and page.total_matches == len(ASSET_CATALOG)
        assert page.offset == offset
        items.extend(item.asset_id for item in page.items)
        if page.next_offset is None:
            break
        assert page.next_offset == offset + 7
        offset = page.next_offset
    assert items == sorted(ASSET_CATALOG)
    assert search_furniture(CatalogSearch(offset=10_000)).items == ()
    assert search_furniture(CatalogSearch(offset=10_000)).next_offset is None


def test_text_type_category_and_mount_filters_intersect_and_are_case_insensitive():
    result = search_furniture(CatalogSearch(query="OFFICE chair", category="furniture", object_type="chair", placement="floor"))
    assert [item.asset_id for item in result.items] == ["chair_office_01"]
    assert search_furniture(CatalogSearch(query="no_such_roometry_asset")).total_matches == 0
    assert search_furniture(CatalogSearch(category="bathroom", object_type="computer")).items == ()
    assert search_furniture(CatalogSearch(query="   ")).total_matches == len(ASSET_CATALOG)


def test_dimension_filters_use_complete_mount_specific_dimensions_without_swapping_axes():
    result = search_furniture(CatalogSearch(object_type="tv", max_depth_m=.1, max_height_m=.7))
    assert result.total_matches == 1
    assert set(result.items[0].configurations) == {"wall"}
    assert result.items[0].configurations["wall"].depth == .06
    assert search_furniture(CatalogSearch(object_type="tv", placement="surface", max_depth_m=.1)).items == ()
    # Standing frames are narrower but deeper: do not mix wall depth with surface width.
    assert search_furniture(CatalogSearch(object_type="picture", max_width_m=.3, max_depth_m=.1)).items == ()
    assert search_furniture(CatalogSearch(object_type="tv", max_width_m=1.19)).items == ()
    assert search_furniture(CatalogSearch(object_type="projector", placement="ceiling", max_height_m=.2)).items == ()


@pytest.mark.parametrize("patch", [
    {"limit":0}, {"limit":21}, {"limit":True}, {"limit":"8"}, {"limit":1.5},
    {"offset":-1}, {"offset":10_001}, {"offset":False},
    {"max_width_m":0}, {"max_height_m":-1}, {"max_depth_m":31},
    {"max_width_m":float("nan")}, {"max_depth_m":float("inf")}, {"max_width_m":"2"},
    {"query":"x"*121}, {"query":123}, {"category":"retailer"},
    {"object_type":"invented_sofa"}, {"placement":"roof"}, {"budget":100},
])
def test_search_parameters_are_bounded_and_unknown_filters_are_rejected(patch):
    with pytest.raises(ValidationError):
        CatalogSearch.model_validate(patch)


@pytest.mark.parametrize("asset_id", sorted(ASSET_CATALOG))
def test_details_match_authoritative_dimensions_mounting_support_and_variants(asset_id):
    asset, definition = ASSET_CATALOG[asset_id], ASSET_REGISTRY[asset_id]
    details = get_furniture_details(asset_id)
    assert details.item.asset_id == asset_id
    assert details.item.object_type == asset.object_type
    assert details.item.color == asset.color and details.item.dimensions_source == "catalog_nominal"
    assert details.item.fixed_by_default == definition.fixed_by_default
    assert set(details.configurations) == set(definition.placements)
    assert set(details.variant_asset_ids) == {a.asset_id for a in ASSET_CATALOG.values() if a.object_type == asset.object_type and a.asset_id != asset_id}
    for placement, configuration in details.configurations.items():
        width, height, depth = dimensions_for(asset_id, placement)
        assert configuration.dimensions_m.model_dump() == dict(width=width,height=height,depth=depth)
        assert details.item.configurations[placement] == configuration.dimensions_m
        anchor = asset.to_dict()["placement_anchors"][placement]
        assert configuration.anchor_at_normalized == anchor["at"]
        assert configuration.anchor_normal == anchor["normal"]
        expected = object_surfaces(asset_id, placement)
        assert len(configuration.support_surfaces) == len(expected)
        for area, reference in zip(configuration.support_surfaces, expected):
            assert area.name == reference.name and area.part == reference.part
            assert area.height_m == pytest.approx(reference.y * height)
            assert (area.x_min_m,area.x_max_m) == pytest.approx((reference.x_min*width,reference.x_max*width))
            assert (area.z_min_m,area.z_max_m) == pytest.approx((reference.z_min*depth,reference.z_max*depth))
            assert -.5*width <= area.x_min_m < area.x_max_m <= .5*width
            assert -.5*depth <= area.z_min_m < area.z_max_m <= .5*depth
    assert FurnitureDetails.model_validate_json(details.model_dump_json()) == details
    assert "price" not in details.model_dump_json() and "parts" not in details.model_dump()


def test_results_are_independent_and_cannot_change_shared_models_or_future_queries():
    asset = ASSET_CATALOG["desk_standard_01"]
    original = deepcopy(asset.to_dict())
    result = get_furniture_details(asset.asset_id)
    result.configurations.clear()
    result.item.configurations.clear()
    assert get_furniture_details(asset.asset_id).configurations
    assert asset.to_dict() == original
    with pytest.raises(CatalogItemNotFound, match="catalog_item_not_found"):
        get_furniture_details("missing_asset")


def test_category_counts_cover_the_single_catalog_with_no_duplicate_assets():
    result = list_furniture_categories()
    assert result.total_items == len(ASSET_CATALOG)
    assert sum(category.item_count for category in result.categories) == result.total_items
    assert [c.category for c in result.categories] == sorted({a.category for a in ASSET_CATALOG.values()})
    for category in result.categories:
        assert set(category.object_types) == {a.object_type for a in ASSET_CATALOG.values() if a.category == category.category}


@pytest.mark.anyio
async def test_mcp_discovery_advertises_only_read_only_tools_with_valid_input_output_schemas(client):
    tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == {"search_furniture", "get_furniture_details", "list_furniture_categories"}
    for tool in tools:
        assert tool.annotations.read_only_hint and tool.annotations.open_world_hint is False
        assert tool.description and tool.output_schema
        Draft202012Validator.check_schema(tool.input_schema)
        Draft202012Validator.check_schema(tool.output_schema)
    assert not (await client.list_resources()).resources
    assert not (await client.list_prompts()).prompts


@pytest.mark.anyio
@pytest.mark.parametrize("name,args,model", [
    ("search_furniture", {"filters":{"object_type":"sofa","limit":2}}, SearchResults),
    ("get_furniture_details", {"asset_id":"desk_standard_01"}, FurnitureDetails),
    ("list_furniture_categories", {}, CatalogCategories),
])
async def test_protocol_outputs_obey_the_advertised_schemas_and_match_direct_catalog(client,name,args,model):
    result = await client.call_tool(name,args)
    parsed = decode(model,result)
    tool = next(t for t in (await client.list_tools()).tools if t.name == name)
    Draft202012Validator(tool.output_schema).validate(result.structured_content)
    direct = {"search_furniture":lambda:search_furniture(CatalogSearch(object_type="sofa",limit=2)),
              "get_furniture_details":lambda:get_furniture_details("desk_standard_01"),
              "list_furniture_categories":list_furniture_categories}[name]()
    assert parsed == direct


@pytest.mark.anyio
@pytest.mark.parametrize("name,args", [
    ("search_furniture",{"filters":{"limit":21}}),
    ("search_furniture",{"filters":{"limit":"2"}}),
    ("search_furniture",{"filters":{"query":"x"*121}}),
    ("search_furniture",{"filters":{"max_width_m":-1}}),
    ("search_furniture",{"filters":{"category":"invented"}}),
    ("search_furniture",{"filters":{"command":"do not execute"}}),
    ("get_furniture_details",{}),
    ("get_furniture_details",{"asset_id":"../../outside"}),
    ("get_furniture_details",{"asset_id":"missing_asset"}),
    ("delete_furniture",{"asset_id":"desk_standard_01"}),
])
async def test_bad_calls_are_explicit_errors_and_leave_the_connection_usable(client,name,args):
    result = await client.call_tool(name,args)
    assert result.is_error and result.content
    if args.get("asset_id") == "missing_asset":
        assert "catalog_item_not_found" in result.content[0].text
    assert decode(CatalogCategories, await client.call_tool("list_furniture_categories",{})).total_items == len(ASSET_CATALOG)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_real_stdio_process_discovers_calls_and_recovers_without_api_credentials(tmp_path,mode):
    # Run outside the repo: the installed package must work without a .env, cwd
    # data files, HTTP server, provider credentials, or raw user photos.
    params = StdioServerParameters(
        command=sys.executable, args=["-m","roometry.mcp.furniture_server"],
        cwd=tmp_path, env={"PYTHONUNBUFFERED":"1"},
    )
    with anyio.fail_after(20):
        async with Client(params,mode=mode,read_timeout_seconds=5) as connected:
            assert {tool.name for tool in (await connected.list_tools()).tools} == {
                "search_furniture","get_furniture_details","list_furniture_categories",
            }
            page = decode(SearchResults,await connected.call_tool("search_furniture",{"filters":{"object_type":"tv","max_depth_m":.1}}))
            assert page.total_matches == 1 and set(page.items[0].configurations) == {"wall"}
            detail = decode(FurnitureDetails,await connected.call_tool("get_furniture_details",{"asset_id":page.items[0].asset_id}))
            assert set(detail.configurations) == {"surface","wall"}
            assert (await connected.call_tool("get_furniture_details",{"asset_id":"missing_asset"})).is_error
            assert decode(CatalogCategories,await connected.call_tool("list_furniture_categories",{})).total_items == len(ASSET_CATALOG)
