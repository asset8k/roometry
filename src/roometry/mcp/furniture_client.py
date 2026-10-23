"""Bounded, read-only stdio catalog access. Runtime handles never enter checkpoints.

Each interaction owns its subprocess and closes it in the same async task, even
on cancellation. At most two reads per app execute concurrently. Nothing starts
at app import/startup, and no provider credentials or room photos cross MCP.
"""

import json
import math
import sys
from typing import Literal

import anyio
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JSONValidationError
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.shared.exceptions import MCPError
from mcp.types import REQUEST_TIMEOUT
from pydantic import JsonValue, ValidationError

from roometry.mcp.catalog import (
    CatalogCategories,
    CatalogModel,
    CatalogSearch,
    FurnitureDetails,
    ItemId,
    SearchResults,
)
from roometry.scenes.assets import ASSET_REGISTRY

CatalogToolName = Literal[
    "search_furniture", "get_furniture_details", "list_furniture_categories"
]
CATALOG_TOOLS: tuple[CatalogToolName, ...] = (
    "search_furniture",
    "get_furniture_details",
    "list_furniture_categories",
)
RESULT_MODELS: dict[CatalogToolName, type[CatalogModel]] = {
    "search_furniture": SearchResults,
    "get_furniture_details": FurnitureDetails,
    "list_furniture_categories": CatalogCategories,
}


class _SearchRequest(CatalogModel):
    filters: CatalogSearch | None = None


class _DetailsRequest(CatalogModel):
    asset_id: ItemId


REQUEST_MODELS: dict[CatalogToolName, type[CatalogModel]] = {
    "search_furniture": _SearchRequest,
    "get_furniture_details": _DetailsRequest,
    "list_furniture_categories": CatalogModel,
}


class CatalogFailure(Exception):
    """Fixed public codes; never expose stderr, paths, or arbitrary server errors."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _causes(error: BaseException):
    yield error
    if isinstance(error, BaseExceptionGroup):
        for child in error.exceptions:
            yield from _causes(child)


def _encode(value: object, maximum: int) -> str:
    data = json.dumps(value, ensure_ascii=True, allow_nan=False)
    if len(data.encode()) > maximum:
        raise CatalogFailure("catalog_response_too_large")
    return data


class FurnitureCatalogClient:
    def __init__(self, timeout_seconds: float = 8.0, max_response_bytes: int = 65_536):
        if not math.isfinite(timeout_seconds) or not 0.1 <= timeout_seconds <= 30:
            raise ValueError("Catalog timeout must be between .1 and 30 seconds.")
        if not 4096 <= max_response_bytes <= 262_144:
            raise ValueError(
                "Catalog response limit must be between 4096 and 262144 bytes."
            )
        self.timeout_seconds, self.max_response_bytes = (
            timeout_seconds,
            max_response_bytes,
        )
        self._capacity = anyio.CapacityLimiter(2)

    async def call(
        self, name: CatalogToolName, arguments: dict[str, JsonValue]
    ) -> CatalogModel:
        if name not in CATALOG_TOOLS:
            raise CatalogFailure("catalog_tool_not_allowed")
        try:
            REQUEST_MODELS[name].model_validate(arguments)
        except ValidationError:
            raise CatalogFailure("catalog_invalid_request") from None
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "roometry.mcp.furniture_server"],
            # The SDK only adds its small, safe environment allowlist. Do not
            # pass os.environ: it may contain OpenAI keys or database passwords.
            env={"PYTHONUNBUFFERED": "1"},
        )
        try:
            # Covers queueing, startup/discovery, invocation and normal shutdown.
            with anyio.fail_after(self.timeout_seconds):
                async with self._capacity:
                    async with Client(
                        params, read_timeout_seconds=self.timeout_seconds
                    ) as client:
                        discovery = await client.list_tools()
                        _encode(discovery.model_dump(mode="json"), 131_072)
                        tools = {tool.name: tool for tool in discovery.tools}
                        if (
                            discovery.next_cursor is not None
                            or len(discovery.tools) != len(CATALOG_TOOLS)
                            or set(tools) != set(CATALOG_TOOLS)
                        ):
                            raise CatalogFailure("catalog_protocol_error")
                        for tool in tools.values():
                            if (
                                not tool.annotations
                                or tool.annotations.read_only_hint is not True
                                or tool.annotations.open_world_hint is not False
                                or not tool.output_schema
                            ):
                                raise CatalogFailure("catalog_protocol_error")
                        tool = tools[name]
                        # Discover the actual schemas before sending anything;
                        # only application-owned, narrow tool arguments arrive here.
                        Draft202012Validator(tool.input_schema).validate(arguments)
                        response = await client.call_tool(name, arguments)
                        _encode(
                            response.model_dump(mode="json"), self.max_response_bytes
                        )
                        if response.is_error:
                            missing = name == "get_furniture_details" and any(
                                getattr(part, "text", "").startswith(
                                    (
                                        "catalog_item_not_found:",
                                        "Error executing tool get_furniture_details: catalog_item_not_found:",
                                    )
                                )
                                for part in response.content
                            )
                            raise CatalogFailure(
                                "catalog_item_not_found"
                                if missing
                                else "catalog_tool_error"
                            )
                        if response.structured_content is None:
                            raise CatalogFailure("catalog_invalid_response")
                        data = _encode(
                            response.structured_content, self.max_response_bytes
                        )
                        try:
                            parsed = RESULT_MODELS[name].model_validate_json(data)
                            Draft202012Validator(tool.output_schema).validate(
                                response.structured_content
                            )
                            self._check_result(parsed, arguments)
                        except CatalogFailure:
                            raise
                        except (ValueError, TypeError, JSONValidationError) as error:
                            raise CatalogFailure("catalog_invalid_response") from error
                        return parsed
        except Exception as error:
            causes = tuple(_causes(error))
            known = next((e for e in causes if isinstance(e, CatalogFailure)), None)
            if known is not None:
                raise known from None
            if any(
                isinstance(e, TimeoutError)
                or (isinstance(e, MCPError) and e.code == REQUEST_TIMEOUT)
                for e in causes
            ):
                raise CatalogFailure("catalog_timeout") from None
            raise CatalogFailure("catalog_unavailable") from None

    @staticmethod
    def _check_result(result: CatalogModel, arguments: dict[str, JsonValue]) -> None:
        """Reject wrong request identities or unapproved assets before model feedback."""
        if isinstance(result, SearchResults):
            filters = CatalogSearch.model_validate(arguments.get("filters") or {})
            if (
                result.offset != filters.offset
                or len(result.items) > filters.limit
                or len({i.asset_id for i in result.items}) != len(result.items)
                or (
                    bool(result.items)
                    and result.total_matches < result.offset + len(result.items)
                )
            ):
                raise CatalogFailure("catalog_invalid_response")
            if result.next_offset is not None and (
                not result.items
                or result.next_offset != result.offset + len(result.items)
                or result.next_offset >= result.total_matches
            ):
                raise CatalogFailure("catalog_invalid_response")
            for item in result.items:
                definition = ASSET_REGISTRY.get(item.asset_id)
                if (
                    definition is None
                    or definition.object_type != item.object_type
                    or not item.configurations
                    or not set(item.configurations) <= definition.placements
                    or filters.object_type is not None
                    and item.object_type != filters.object_type
                    or filters.category is not None
                    and item.category != filters.category
                    or filters.placement is not None
                    and set(item.configurations) != {filters.placement}
                ):
                    raise CatalogFailure("catalog_invalid_response")
                for size in item.configurations.values():
                    if any(
                        maximum is not None and getattr(size, axis) > maximum
                        for axis, maximum in (
                            ("width", filters.max_width_m),
                            ("height", filters.max_height_m),
                            ("depth", filters.max_depth_m),
                        )
                    ):
                        raise CatalogFailure("catalog_invalid_response")
        elif isinstance(result, FurnitureDetails):
            definition = ASSET_REGISTRY.get(result.item.asset_id)
            if (
                result.item.asset_id != arguments.get("asset_id")
                or definition is None
                or result.item.object_type != definition.object_type
                or set(result.configurations) != set(definition.placements)
                or set(result.item.configurations) != set(result.configurations)
                or any(
                    config.dimensions_m != result.item.configurations[p]
                    for p, config in result.configurations.items()
                )
                or any(
                    v not in ASSET_REGISTRY
                    or ASSET_REGISTRY[v].object_type != result.item.object_type
                    for v in result.variant_asset_ids
                )
            ):
                raise CatalogFailure("catalog_invalid_response")
