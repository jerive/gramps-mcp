"""Adapts the backend's OpenAPI spec to the tools a tree exposes.

The spec is read from the running Gramps Web API, so the tools follow the
server that is actually deployed. Two things are done to it here:

* the version is checked against the range this package is tested with;
* in `core` mode, the per-type routes (`/api/people/`, `/api/families/`...) are
  collapsed into three generic operations with an `object_type` parameter. The
  per-type operations are identical apart from the path, so ten copies of each
  would only cost context. A type whose operation differs from the others
  (a future Gramps version) keeps its own routes.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any, Literal

import httpx2

from .core import GRAMPS_BACKEND_URL, logger

# The oldest Gramps Web API this package works with: the `query` endpoints
# arrived in 3.21 and GOQL's `MatchesQuery` in 3.23.
MIN_API_VERSION = (3, 23)
# The newest one it is tested against. Newer versions are used with a warning.
TESTED_API_VERSION = (3, 24)

OBJECT_TYPE_PARAM = "object_type"
_TEMPLATE_TYPE = "people"

# (generic path, method, name of the tool, what it does)
_GENERIC = (
    (
        "/api/{t}/",
        "get",
        "list_objects",
        "List the objects of one type, with filters, sorting and paging.",
    ),
    (
        "/api/{t}/{handle}",
        "get",
        "get_object",
        "Get one object of any type from its handle.",
    ),
    (
        "/api/{t}/query/",
        "post",
        "query_objects",
        "Query the objects of one type with a structured, GOQL-based request.",
    ),
)


def fetch_openapi_spec() -> dict[str, Any]:
    """Reads the OpenAPI document of the running Gramps Web API."""
    url = f"{GRAMPS_BACKEND_URL}/api/openapi.json"
    spec: dict[str, Any] = httpx2.get(url, timeout=30).raise_for_status().json()
    return spec


class UnsupportedGrampsVersion(RuntimeError):
    pass


def parse_version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value)[:3])


def check_api_version(spec: dict[str, Any]) -> str:
    """Returns the backend's API version, refusing one older than supported."""
    raw = str(spec.get("info", {}).get("version", ""))
    try:
        version = parse_version(raw)
    except ValueError:
        version = ()
    if not version:
        logger.warning("Gramps Web API reports no usable version (%r)", raw)
        return raw
    if version[:2] < MIN_API_VERSION:
        needed = ".".join(map(str, MIN_API_VERSION))
        raise UnsupportedGrampsVersion(
            f"Gramps Web API {raw} is too old: gramps-mcp needs {needed} or newer."
        )
    if version[:2] > TESTED_API_VERSION:
        tested = ".".join(map(str, TESTED_API_VERSION))
        logger.warning(
            "Gramps Web API %s is newer than %s, the latest version gramps-mcp is "
            "tested with; continuing anyway.",
            raw,
            tested,
        )
    else:
        logger.info("Gramps Web API %s", raw)
    return raw


def _at(path: str, object_type: str) -> str:
    return path.replace("{t}", object_type)


_METHODS = {"get", "put", "post", "delete", "patch", "options", "head"}


def _operation(spec: dict[str, Any], path: str, method: str) -> dict[str, Any] | None:
    operation = spec.get("paths", {}).get(path, {}).get(method)
    return operation if isinstance(operation, dict) else None


def _shape(operation: dict[str, Any]) -> Any:
    """What makes two per-type operations interchangeable: all but their names."""
    keep = {
        k: v
        for k, v in operation.items()
        if k not in {"operationId", "summary", "description", "tags"}
    }
    return keep


def collapse_object_routes(
    spec: dict[str, Any], object_types: tuple[str, ...]
) -> dict[str, Any]:
    """Returns a copy of `spec` where the per-type routes are made generic.

    Only the types that have the same list, get and query operations as
    `people` are merged; the others are left as they are.
    """
    result = copy.deepcopy(spec)
    paths = result["paths"]
    templates: dict[str, dict[str, Any]] = {}
    for path, method, name, summary in _GENERIC:
        template = _operation(spec, _at(path, _TEMPLATE_TYPE), method)
        if template is None:
            logger.warning("Gramps Web API has no %s %s", method, path)
            return result
        templates[name] = template

    merged = [
        t
        for t in object_types
        if all(
            (other := _operation(spec, _at(path, t), method)) is not None
            and _shape(other) == _shape(templates[name])
            for path, method, name, _ in _GENERIC
        )
    ]
    if len(merged) < 2:
        return result

    shared = {
        path: copy.deepcopy(
            spec["paths"][_at(path, _TEMPLATE_TYPE)].get("parameters", [])
        )
        for path, _, _, _ in _GENERIC
    }
    for t in merged:
        for path, method, _, _ in _GENERIC:
            route = paths.get(_at(path, t), {})
            route.pop(method, None)
            if not _METHODS & route.keys():
                paths.pop(_at(path, t), None)

    for path, method, name, summary in _GENERIC:
        operation = copy.deepcopy(templates[name])
        operation["operationId"] = name
        operation["summary"] = summary
        operation["description"] = (
            f"{summary} `{OBJECT_TYPE_PARAM}` is one of: {', '.join(merged)}."
        )
        operation.pop("tags", None)
        operation["parameters"] = [
            {
                "name": OBJECT_TYPE_PARAM,
                "in": "path",
                "required": True,
                "description": "The kind of object.",
                "schema": {"type": "string", "enum": list(merged)},
            },
            *operation.get("parameters", []),
            *shared[path],
        ]
        paths.setdefault(_at(path, f"{{{OBJECT_TYPE_PARAM}}}"), {})[method] = operation
    return result


TREE_PARAM = "tree"


def add_tree_parameter(spec: dict[str, Any], tree_ids: list[str]) -> dict[str, Any]:
    """A copy of `spec` where every operation takes the `tree` it is for. It is
    required, unless there is a single tree. The server removes it from the
    request: Gramps never sees it."""
    result = copy.deepcopy(spec)
    parameter: dict[str, Any] = {
        "name": TREE_PARAM,
        "in": "query",
        "required": len(tree_ids) > 1,
        "description": "The tree to read. See list_trees.",
        "schema": {"type": "string", "enum": list(tree_ids)},
    }
    for item in result.get("paths", {}).values():
        for method, operation in item.items():
            if method in _METHODS and isinstance(operation, dict):
                operation["parameters"] = [*operation.get("parameters", []), parameter]
    return result


def prepare_spec(
    spec: dict[str, Any],
    mode: Literal["core", "all"],
    object_types: tuple[str, ...],
    tree_ids: list[str],
) -> dict[str, Any]:
    check_api_version(spec)
    prepared = spec if mode == "all" else collapse_object_routes(spec, object_types)
    return add_tree_parameter(prepared, tree_ids)


# Computed by the server, never written by a client.
_READ_ONLY = frozenset({"backlinks", "extended", "profile", "change", "_class"})
_MAX_DEPTH = 6


def object_schemas(
    spec: dict[str, Any], classes: Mapping[str, str]
) -> dict[str, dict[str, Any]]:
    """The schema of each kind of object a client may create, from the backend's
    spec: what `apply_changes` shows the model for the `object` of a `create`.

    `classes` maps a type (`people`) to its schema name (`Person`). References are
    inlined, and what they point to loses its descriptions to keep the size down;
    the fields of the object itself keep theirs. A type the spec does not know is
    left out."""
    components: dict[str, Any] = spec.get("components", {}).get("schemas", {})

    def resolve(node: Any, depth: int = 0) -> Any:
        if isinstance(node, list):
            return [resolve(item, depth) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str):
            target = components.get(ref.rsplit("/", 1)[-1])
            return resolve(target, depth + 1) if target and depth < _MAX_DEPTH else {}
        return {k: resolve(v, depth) for k, v in node.items() if k != "description"}

    schemas: dict[str, dict[str, Any]] = {}
    for type_, name in classes.items():
        schema = components.get(name)
        if not isinstance(schema, dict):
            continue
        properties = {}
        for key, value in schema.get("properties", {}).items():
            if key in _READ_ONLY:
                continue
            described = resolve(value)
            if isinstance(value, dict) and value.get("description"):
                described["description"] = value["description"]
            properties[key] = described
        schemas[type_] = {
            "type": "object",
            "description": f"The object when `type` is {type_!r}.",
            "properties": properties,
        }
    return schemas
