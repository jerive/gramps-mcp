"""Which planned changes depend on which, and which of their values can be edited.

The interactive card lists the changes as a tree, lets the user untick some and
fix a value, then apply. Unticking a change also unticks the changes under it
(a family whose mother is no longer created, a note on a birth that is not), and
ticking one brings back what it hangs under.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
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


# Which changes depend on which is read from the handles a body mentions, by the
# name of the list it sits in, at any depth:
#
# * OWNS: the object owns what it lists, so the listed object needs its owner.
#   Unticking a person unticks their birth, and through it the birth's notes;
#   unticking only the birth leaves the person.
# * LINKS: a back reference or a shared label. The object does not need what it
#   lists, and neither does the other side (a family lists its children, but the
#   child needs its family, not the reverse).
# * anything else (a parent of a family, the family of a child, the place of an
#   event) is a need: the object needs what it mentions.
#
# An object left out is also taken out of the lists that mention it when the
# rest is applied: see `drop_unselected_links`.
OWNS = frozenset({"event_ref_list", "note_list", "citation_list", "media_list"})
LINKS = frozenset({"family_list", "child_ref_list", "tag_list"})

Ops = list[tuple[str, JsonObject | None]]


def _created_handles(ops: Ops) -> dict[str, int]:
    return {
        str(body["handle"]): index
        for index, (op, body) in enumerate(ops)
        if op == "create" and body and body.get("handle")
    }


def _mentions(node: Any, kind: str = "needs") -> Iterator[tuple[str, str]]:
    """Every string in `node` with the kind of list it is in."""
    if isinstance(node, str):
        yield node, kind
    elif isinstance(node, list):
        for item in node:
            yield from _mentions(item, kind)
    elif isinstance(node, dict):
        for key, value in node.items():
            inner = "owns" if key in OWNS else "link" if key in LINKS else kind
            yield from _mentions(value, inner)


def direct_requirements(ops: Ops) -> list[set[int]]:
    """Per change, the changes whose created object it needs."""
    created = _created_handles(ops)
    requirements: list[set[int]] = [set() for _ in ops]
    for index, (_, body) in enumerate(ops):
        for text, kind in _mentions(body):
            other = created.get(text)
            if other is None or other == index:
                continue
            if kind == "needs":
                requirements[index].add(other)
            elif kind == "owns":
                requirements[other].add(index)
    return requirements


def _without(node: Any, left_out: set[str], inside: bool = False) -> Any:
    if isinstance(node, list):
        kept = [
            _without(item, left_out, inside)
            for item in node
            if not (inside and _points_to(item, left_out))
        ]
        return kept if kept != node else node
    if isinstance(node, dict):
        changed = {
            key: _without(value, left_out, key in OWNS or key in LINKS or inside)
            for key, value in node.items()
        }
        return changed if changed != node else node
    return node


def _points_to(item: Any, handles: set[str]) -> bool:
    ref = item.get("ref") if isinstance(item, dict) else item
    return isinstance(ref, str) and ref in handles


def drop_unselected_links(ops: Ops, chosen: set[int]) -> list[JsonObject | None]:
    """The bodies to send: a chosen object no longer lists the created objects
    that were unticked, in the lists of OWNS and LINKS. Same order as `ops`; a
    body that needs no change is returned as it is."""
    created = _created_handles(ops)
    left_out = {handle for handle, index in created.items() if index not in chosen}
    if not left_out:
        return [body for _, body in ops]
    return [
        _without(body, left_out) if body is not None and index in chosen else body
        for index, (_, body) in enumerate(ops)
    ]


Outline = list[tuple[int, "Outline"]]


def outline(direct: list[set[int]]) -> Outline:
    """The changes as a tree: what is needed comes first and what needs it hangs
    under it. A change that needs several others hangs under the first of them;
    siblings keep the order they had."""
    count = len(direct)
    children: dict[int | None, list[int]] = {}
    for index in range(count):
        children.setdefault(min(direct[index]) if direct[index] else None, []).append(
            index
        )
    seen: set[int] = set()

    def build(parent: int | None) -> Outline:
        branch: Outline = []
        for index in children.get(parent, []):
            if index not in seen:
                seen.add(index)
                branch.append((index, build(index)))
        return branch

    tree = build(None)
    for index in range(count):  # changes that only need each other
        if index not in seen:
            seen.add(index)
            tree.append((index, build(index)))
    return tree


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
