"""Client deadlines, validation, cancellation and real process behavior."""

import json
import os
import sys
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace

import anyio
import pytest
from mcp import Client
from mcp.types import CallToolResult, TextContent

from roometry.mcp import furniture_client
from roometry.mcp.catalog import CatalogSearch, get_furniture_details, list_furniture_categories, search_furniture
from roometry.mcp.furniture_client import CatalogFailure, FurnitureCatalogClient
from roometry.mcp.furniture_server import server


@pytest.fixture
def anyio_backend():
    return "asyncio"


def response(data):
    return CallToolResult(structured_content=data,content=[TextContent(type="text",text=json.dumps(data))])


@pytest.fixture
async def transport(monkeypatch):
    async with Client(server,raise_exceptions=True) as client:
        discovery = await client.list_tools()
    state = SimpleNamespace(discovery=discovery, entered=0, exited=0, calls=[], params=[], hang=False,
                            data=list_furniture_categories().model_dump(mode="json"), failure=None, active=0, peak=0, delay=0.)

    class Connection:
        async def list_tools(self):
            if state.hang:
                await anyio.sleep_forever()
            return state.discovery

        async def call_tool(self,name,args):
            state.calls.append((name,args))
            await anyio.sleep(state.delay)
            if state.failure:
                raise state.failure
            return response(state.data)

    @asynccontextmanager
    async def factory(params,**kwargs):
        state.params.append(params)
        state.entered += 1
        state.active += 1
        state.peak = max(state.peak,state.active)
        try:
            yield Connection()
        finally:
            state.exited += 1
            state.active -= 1

    monkeypatch.setattr(furniture_client,"Client",factory)
    return state


@pytest.mark.anyio
async def test_real_client_executes_all_three_mcp_tools_without_provider_calls():
    client = FurnitureCatalogClient()
    found = await client.call("search_furniture",{"filters":{"object_type":"picture","placement":"surface"}})
    assert found == search_furniture(CatalogSearch(object_type="picture",placement="surface"))
    detail = await client.call("get_furniture_details",{"asset_id":"desk_standard_01"})
    assert detail == get_furniture_details("desk_standard_01")
    assert await client.call("list_furniture_categories",{}) == list_furniture_categories()
    with pytest.raises(CatalogFailure,match="catalog_item_not_found"):
        await client.call("get_furniture_details",{"asset_id":"missing_asset"})


@pytest.mark.anyio
async def test_discovery_and_fixed_command_only_send_catalog_arguments_no_private_environment(transport,monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY","PRIVATE_PROVIDER_KEY")
    monkeypatch.setenv("DATABASE_URL","PRIVATE_DATABASE")
    result = await FurnitureCatalogClient().call("list_furniture_categories",{})
    assert result.total_items == list_furniture_categories().total_items
    assert transport.entered == transport.exited == 1
    params = transport.params[0]
    assert params.command == sys.executable and params.args == ["-m","roometry.mcp.furniture_server"]
    assert params.env == {"PYTHONUNBUFFERED":"1"}
    assert transport.calls == [("list_furniture_categories",{})]


@pytest.mark.anyio
async def test_missing_executable_is_sanitized_with_no_retry(monkeypatch):
    def failed(*args,**kwargs):
        raise OSError("PRIVATE_PATH /Users/private and secret")
    monkeypatch.setattr(furniture_client,"Client",failed)
    with pytest.raises(CatalogFailure) as error:
        await FurnitureCatalogClient().call("list_furniture_categories",{})
    assert str(error.value) == error.value.code == "catalog_unavailable"


@pytest.mark.anyio
async def test_deadline_covers_discovery_and_closes_context_without_retry(transport):
    transport.hang = True
    with pytest.raises(CatalogFailure,match="catalog_timeout"):
        await FurnitureCatalogClient(timeout_seconds=.1).call("list_furniture_categories",{})
    assert transport.entered == transport.exited == 1 and not transport.calls


@pytest.mark.anyio
async def test_external_cancellation_is_not_converted_to_an_mcp_failure(transport):
    transport.hang = True
    with anyio.move_on_after(.05) as scope:
        await FurnitureCatalogClient().call("list_furniture_categories",{})
    assert scope.cancel_called and transport.entered == transport.exited == 1


@pytest.mark.anyio
@pytest.mark.parametrize("bad",["missing","extra","writable","untyped","pagination"])
async def test_unexpected_discovery_is_rejected_before_invocation(transport,bad):
    tools = transport.discovery.tools
    if bad == "missing":
        tools.pop()
    elif bad == "extra":
        tool = deepcopy(tools[0]); tool.name="run_shell"; tools.append(tool)
    elif bad == "writable":
        tools[0].annotations.read_only_hint=False
    elif bad == "untyped":
        tools[0].output_schema=None
    else:
        transport.discovery.next_cursor="more"
    with pytest.raises(CatalogFailure,match="catalog_protocol_error"):
        await FurnitureCatalogClient().call("list_furniture_categories",{})
    assert not transport.calls and transport.entered == transport.exited


@pytest.mark.anyio
@pytest.mark.parametrize("data",[{}, {"schema_version":"catalog-v1","total_items":1,"categories":[],"extra":"PRIVATE"}])
async def test_malformed_structured_results_do_not_reach_the_agent(transport,data):
    transport.data=data
    with pytest.raises(CatalogFailure,match="catalog_invalid_response"):
        await FurnitureCatalogClient().call("list_furniture_categories",{})
    assert transport.entered == transport.exited


@pytest.mark.anyio
async def test_oversized_response_is_rejected(transport):
    transport.data={"PRIVATE":"x"*5000}
    with pytest.raises(CatalogFailure,match="catalog_response_too_large"):
        await FurnitureCatalogClient(max_response_bytes=4096).call("list_furniture_categories",{})


@pytest.mark.anyio
@pytest.mark.parametrize("bad",["identity","unknown_asset","mount","nominal_sizes"])
async def test_schema_valid_but_wrong_detail_metadata_is_rejected(transport,bad):
    transport.data=get_furniture_details("tv_standard_01").model_dump(mode="json")
    args={"asset_id":"tv_standard_01"}
    if bad == "identity":
        args["asset_id"]="desk_standard_01"
    elif bad == "unknown_asset":
        transport.data["item"]["asset_id"]="invented_tv"; args["asset_id"]="invented_tv"
    elif bad == "mount":
        transport.data["configurations"].pop("wall")
    else:
        transport.data["item"]["configurations"]["wall"]["width"] = 2.
    with pytest.raises(CatalogFailure,match="catalog_invalid_response"):
        await FurnitureCatalogClient().call("get_furniture_details",args)


@pytest.mark.anyio
async def test_search_cannot_return_models_violating_requested_mount_and_size_filters(transport):
    transport.data=search_furniture(CatalogSearch(object_type="tv")).model_dump(mode="json")
    with pytest.raises(CatalogFailure,match="catalog_invalid_response"):
        await FurnitureCatalogClient().call("search_furniture",{"filters":{"object_type":"tv","placement":"wall","max_depth_m":.1}})


@pytest.mark.anyio
@pytest.mark.parametrize("name,args",[("run_shell",{}),("get_furniture_details",{"asset_id":"desk_standard_01","path":"PRIVATE"}),("search_furniture",{"filters":{"limit":100}})])
async def test_untrusted_names_and_authority_fields_are_rejected_without_starting_process(transport,name,args):
    with pytest.raises(CatalogFailure,match="catalog_tool_not_allowed|catalog_invalid_request"):
        await FurnitureCatalogClient().call(name,args)
    assert transport.entered == 0


@pytest.mark.parametrize("kwargs",[{"timeout_seconds":0},{"timeout_seconds":float("nan")},{"max_response_bytes":1}])
def test_invalid_client_limits_are_rejected(kwargs):
    with pytest.raises(ValueError):
        FurnitureCatalogClient(**kwargs)


@pytest.mark.anyio
async def test_app_capacity_bounds_subprocesses_and_releases_slots(transport):
    transport.delay=.03
    client=FurnitureCatalogClient()
    async def read():
        await client.call("list_furniture_categories",{})
    async with anyio.create_task_group() as tasks:
        for _ in range(6):
            tasks.start_soon(read)
    assert transport.peak == 2 and transport.entered == transport.exited == 6


@pytest.mark.anyio
async def test_timeout_terminates_a_real_unresponsive_child(monkeypatch,tmp_path):
    pid_file=tmp_path / "catalog.pid"
    actual=furniture_client.Client
    def sleeping_process(params,**kwargs):
        script="import os,sys,time; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(30)"
        return actual(params.model_copy(update={"args":["-c",script,str(pid_file)]}),**kwargs)
    monkeypatch.setattr(furniture_client,"Client",sleeping_process)
    with anyio.fail_after(5):
        with pytest.raises(CatalogFailure,match="catalog_timeout"):
            await FurnitureCatalogClient(timeout_seconds=.5).call("list_furniture_categories",{})
    assert pid_file.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()),0)
