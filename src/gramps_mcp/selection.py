"""Which planned changes depend on which, and which of their values can be edited.

The interactive card lets the user untick changes and fix a value before
applying. Unticking a change must also drop the changes that need it (a family
that cites a person that is no longer created), and ticking one must bring back
what it needs. A change needs another when its body mentions the handle that
the other one creates.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .summary import diff_paths

JsonObject = dict[str, Any]

MAX_EDIT_LENGTH = 500
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# type -> (path into the object, label). Only free text with no structural
# meaning: a name, a title, a description.
EDITABLE: dict[str, tuple[tuple[str, str], ...]] = {
    "people": (
        ("primary_name.first_name", "first name"),
        ("primary_name.surname_list.0.surname", "surname"),
    ),
    "events": (("description", "description"),),
    "places": (("name.value", "name"),),
    "notes": (("text.string", "text"),),
    "sources": (("title", "title"),),
    "citations": (("page", "page"),),
    "repositories": (("name", "name"),),
    "tags": (("name", "name"),),
}


class EditError(ValueError):
    """An edit that cannot be accepted; the message is safe to show."""


@dataclass(frozen=True)
class EditField:
    key: str
    op: int
    path: str
    label: str
    value: str


def _step(node: Any, segment: str) -> Any:
    if isinstance(node, list):
        return (
            node[int(segment)]
            if segment.isdigit() and int(segment) < len(node)
            else None
        )
    if isinstance(node, dict):
        return node.get(segment)
    return None


def get_path(obj: JsonObject, path: str) -> Any:
    node: Any = obj
    for segment in path.split("."):
        node = _step(node, segment)
        if node is None:
            return None
    return node


def set_path(obj: JsonObject, path: str, value: str) -> None:
    *parents, last = path.split(".")
    node: Any = obj
    for segment in parents:
        node = _step(node, segment)
        if node is None:
            raise EditError(f"There is nothing to edit at {path!r}.")
    if isinstance(node, list) and last.isdigit() and int(last) < len(node):
        node[int(last)] = value
    elif isinstance(node, dict):
        node[last] = value
    else:
        raise EditError(f"There is nothing to edit at {path!r}.")


def check_edit(value: str) -> str:
    if not isinstance(value, str):
        raise EditError("An edited value must be text.")
    if len(value) > MAX_EDIT_LENGTH:
        raise EditError(f"An edited value is limited to {MAX_EDIT_LENGTH} characters.")
    if CONTROL_CHARS.search(value):
        raise EditError("An edited value cannot contain control characters.")
    return value.strip()


def edit_fields(
    ops: list[tuple[str, str, JsonObject | None, JsonObject | None]],
) -> list[EditField]:
    """The values the user may edit, in a stable order that the server rebuilds.

    `ops` is, per planned change, (op, type, body, current). A created object
    offers every editable field it has; an edited one only the fields that its
    change touches.
    """
    fields: list[EditField] = []
    for index, (op, type_, body, current) in enumerate(ops):
        if op == "delete" or body is None:
            continue
        touched = (
            {path for path, _, _ in diff_paths(current or {}, body)}
            if op == "update"
            else None
        )
        for path, label in EDITABLE.get(type_, ()):
            if touched is not None and path not in touched:
                continue
            value = get_path(body, path)
            if isinstance(value, str):
                fields.append(EditField(f"ed{len(fields)}", index, path, label, value))
    return fields


def direct_requirements(
    ops: list[tuple[str, JsonObject | None]],
) -> list[set[int]]:
    """Per change, the changes whose created handle it mentions."""
    created = {
        str(body["handle"]): index
        for index, (op, body) in enumerate(ops)
        if op == "create" and body and body.get("handle")
    }
    requirements: list[set[int]] = []
    for index, (_, body) in enumerate(ops):
        text = json.dumps(body, ensure_ascii=False) if body else ""
        requirements.append(
            {j for handle, j in created.items() if j != index and f'"{handle}"' in text}
        )
    return requirements


def closure(direct: list[set[int]]) -> tuple[list[set[int]], list[set[int]]]:
    """(requires, required_by): everything each change needs, and everything
    that needs it, following the dependencies as far as they go."""
    count = len(direct)
    requires: list[set[int]] = []
    for start in range(count):
        seen: set[int] = set()
        stack = list(direct[start])
        while stack:
            node = stack.pop()
            if node not in seen and node != start:
                seen.add(node)
                stack.extend(direct[node])
        requires.append(seen)
    required_by = [{j for j in range(count) if i in requires[j]} for i in range(count)]
    return requires, required_by


def missing_requirements(
    selected: set[int], requires: list[set[int]]
) -> list[tuple[int, int]]:
    """(change, needed change) pairs where a selected change lacks what it needs."""
    return [
        (i, needed)
        for i in sorted(selected)
        for needed in sorted(requires[i] - selected)
    ]
