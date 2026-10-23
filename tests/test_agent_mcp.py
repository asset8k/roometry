"""LangGraph + owned uploads + genuine catalog subprocesses; mocked model HTTP."""

from math import pi

import pytest

from roometry.agents.state import CatalogRead
from roometry.agents.tool_policy import catalog_available, catalog_already_read
from roometry.agents.tools import available_tools
from roometry.mcp import furniture_client, catalog
from roometry.mcp.furniture_client import CATALOG_TOOLS, CatalogFailure
from tests import test_vision_api
from tests.test_agent_tools import catalog_arguments, context, decision, prepared, stages
from tests.test_agent_reliability import populated

anyio_backend = test_vision_api.anyio_backend
isolated_environment = test_vision_api.isolated_environment
harness = test_vision_api.harness
pytestmark = pytest.mark.anyio


async def test_model_searches_two_pages_and_two_details_through_mcp_then_validates_and_finishes(harness,monkeypatch):
    app,_,calls,actions = harness
    starts=[]
    actual = furniture_client.Client
    def subprocess_client(params,**kwargs):
        starts.append(params)
        return actual(params,**kwargs)
    monkeypatch.setattr(furniture_client,"Client",subprocess_client)
    # An in-process lookup would fail: child processes load their own catalog.
    def no_local_query(*args,**kwargs):
        raise AssertionError("The agent must query MCP, not a local catalog function")
    monkeypatch.setattr(catalog,"search_furniture",no_local_query)
    runs,owner,uploaded = await prepared(harness)
    stages(actions)
    query = catalog_arguments(object_type="picture",placement="surface",limit=1)
    detail = {"asset_id":"picture_standard_01"}
    actions.extend([
        decision("list_furniture_categories"),
        decision("search_furniture",query),
        decision("search_furniture",{**query,"offset":1}),
        decision("get_furniture_details",detail),
        decision("get_furniture_details",{"asset_id":"picture_rectangular_01"}),
        decision("get_furniture_details",detail),  # Must reuse previous metadata.
        decision("rotate_object",{"object_id":"chair_a","rotation_y_rad":pi/2}),
        decision("validate_scene"), decision("finish"),
    ])
    initial=await runs.create(uploaded["input_id"],owner)
    result=await runs.execute(initial.run_id,owner)
    assert result.status == "completed",result.model_dump_json()
    assert len(starts) == len(result.catalog_reads) == 5
    assert all(read.outcome == "success" for read in result.catalog_reads)
    searches=[f for f in result.tool_feedback if f.name == "search_furniture"]
    assert all(f.details["transport"] == "stdio" for f in searches)
    assert searches[0].details["result"]["next_offset"] == 1
    assert searches[0].details["result"]["items"][0]["asset_id"] != searches[1].details["result"]["items"][0]["asset_id"]
    assert any(f.details.get("code") == "catalog_duplicate_request" for f in result.tool_feedback)
    assert next(o for o in result.working_scene.objects if o.id == "chair_a").rotation_y_rad == pi/2
    assert next(o for o in result.base_scene.objects if o.id == "chair_a").rotation_y_rad == 0
    assert result.usage.tool_calls == 11 and result.usage.model_calls == len(calls) == 13
    assert [r.stage for r in app.state.analysis_service.usage_records] == ["analysis","scene_proposal"]
    assert context(calls[-1])["catalog_reads"] == [r.model_dump(mode="json") for r in result.catalog_reads]
    assert (await runs.execute(initial.run_id,owner)) == result
    assert len(starts) == 5 and len(calls) == 13


@pytest.mark.parametrize("failure",["catalog_timeout","catalog_unavailable","catalog_invalid_response"])
async def test_catalog_failure_preserves_scene_disables_reads_and_finishes_honestly(harness,monkeypatch,failure):
    _,_,calls,actions=harness
    attempts=[]
    async def failed(self,name,args):
        attempts.append((name,args))
        raise CatalogFailure(failure)
    monkeypatch.setattr(furniture_client.FurnitureCatalogClient,"call",failed)
    runs,owner,uploaded=await prepared(harness)
    stages(actions)
    actions.extend([decision("search_furniture",catalog_arguments(object_type="bed")),decision("finish")])
    initial=await runs.create(uploaded["input_id"],owner)
    result=await runs.execute(initial.run_id,owner)
    assert result.status == "completed" and result.last_error is None
    assert result.working_scene == result.base_scene and len(attempts) == 1
    assert len(result.catalog_reads) == 1 and result.catalog_reads[0].outcome == "error"
    assert result.tool_feedback[-2].details["code"] == failure
    assert not set(CATALOG_TOOLS) & {t["name"] for t in calls[-1]["tools"]}
    assert context(calls[-1])["tool_feedback"][-1]["outcome"] == "error"
    assert result.usage.tool_calls == 4 and result.usage.model_calls == len(calls) == 6


async def test_unknown_item_can_be_corrected_with_a_real_mcp_lookup(harness):
    _,_,calls,actions=harness
    runs,owner,uploaded=await prepared(harness)
    stages(actions)
    actions.extend([decision("get_furniture_details",{"asset_id":"missing_asset"}),
                    decision("get_furniture_details",{"asset_id":"desk_standard_01"}),decision("finish")])
    initial=await runs.create(uploaded["input_id"],owner)
    result=await runs.execute(initial.run_id,owner)
    assert result.status == "completed"
    assert [read.outcome for read in result.catalog_reads] == ["rejected","success"]
    assert result.tool_feedback[-3].details["code"] == "catalog_item_not_found"
    assert result.tool_feedback[-2].details["result"]["item"]["asset_id"] == "desk_standard_01"
    assert result.usage.model_calls == len(calls) == 7


async def test_catalog_read_limits_are_durable_independent_of_feedback_truncation_and_scene_edits():
    state=await populated()
    reads=[CatalogRead(name="search_furniture",arguments=catalog_arguments(offset=i),outcome="success") for i in range(4)]
    state=state.model_copy(update={"catalog_reads":reads,"tool_feedback":[]})
    assert not catalog_available(state,"search_furniture")
    assert "search_furniture" not in available_tools(state)
    assert catalog_available(state,"get_furniture_details")
    assert catalog_already_read(state,"search_furniture",catalog_arguments(offset=2))
    assert not catalog_already_read(state,"search_furniture",catalog_arguments(offset=8))
    state=state.model_copy(update={"catalog_reads":[CatalogRead(name="search_furniture",arguments={},outcome="error")]})
    assert not any(catalog_available(state,name) for name in CATALOG_TOOLS)


async def test_clarification_resume_and_duplicate_answers_never_repeat_an_earlier_mcp_read(harness,monkeypatch):
    _,client,calls,actions=harness
    starts=[]
    actual=furniture_client.Client
    def track(params,**kwargs):
        starts.append(params)
        return actual(params,**kwargs)
    monkeypatch.setattr(furniture_client,"Client",track)
    runs,owner,uploaded=await prepared(harness)
    stages(actions)
    query=catalog_arguments(object_type="chair")
    actions.extend([decision("search_furniture",query),decision("request_clarification",{"kind":"object_mobility","object_id":"chair_a"})])
    initial=await runs.create(uploaded["input_id"],owner)
    paused=await runs.execute(initial.run_id,owner)
    assert paused.status == "awaiting_answer" and len(starts) == len(paused.catalog_reads) == 1
    actions.extend([decision("search_furniture",query),decision("finish")])
    answer={"question_id":paused.pending_question.id,"choice_id":"allow_movement"}
    response=await client.post(f"/api/agent-runs/{initial.run_id}/answer",json=answer)
    assert response.status_code == 200,response.text
    result=response.json()
    assert result["status"] == "completed" and result["catalog_reads"] == [r.model_dump(mode="json") for r in paused.catalog_reads]
    assert result["analysis"] == paused.analysis.model_dump(mode="json")
    assert any(f["details"].get("code") == "catalog_duplicate_request" for f in result["tool_feedback"])
    assert len(starts) == 1
    count=len(calls)
    repeated=await client.post(f"/api/agent-runs/{initial.run_id}/answer",json=answer)
    assert repeated.json() == result and len(calls) == count and len(starts) == 1


async def test_app_startup_and_health_need_no_mcp_process_and_use_configured_limits(monkeypatch,tmp_path):
    import httpx
    from roometry.config import Settings
    from roometry.main import create_app
    starts=[]
    def forbidden(*args,**kwargs):
        starts.append(args)
        raise AssertionError("Health must not start MCP")
    monkeypatch.setattr(furniture_client,"Client",forbidden)
    app=create_app(Settings(_env_file=None,upload_storage_dir=tmp_path / "uploads",
                           mcp_furniture_timeout_seconds=.25,mcp_furniture_max_response_bytes=4096))
    assert app.state.agent_runs.execute_tool.catalog.timeout_seconds == .25
    assert app.state.agent_runs.execute_tool.catalog.max_response_bytes == 4096
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://testserver") as client:
            assert (await client.get("/api/health")).json() == {"status":"ok","service":"roometry"}
    assert not starts
