"""Turns planned changes into text a person can read.

Handles become names (resolved through the API's `profile=self`, or from the
objects created in the same set), field names become words, and values are
shown the way they read, so the confirmation does not show raw JSON.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx2

JsonObject = dict[str, Any]

MAX_TEXT = 60
MAX_DEPTH = 2

SINGULAR = {
    "people": "person",
    "families": "family",
    "events": "event",
    "places": "place",
    "citations": "citation",
    "sources": "source",
    "repositories": "repository",
    "media": "media object",
    "notes": "note",
    "tags": "tag",
}

GENDERS = {0: "female", 1: "male", 2: "unknown", 3: "other"}

# field -> (type of the objects it points to, key holding the handle in a list
# of dicts, or None when the items are plain handles)
REFERENCES: dict[str, tuple[str, str | None]] = {
    "family_list": ("families", None),
    "parent_family_list": ("families", None),
    "child_ref_list": ("people", "ref"),
    "event_ref_list": ("events", "ref"),
    "father_handle": ("people", None),
    "mother_handle": ("people", None),
    "place": ("places", None),
    "tag_list": ("tags", None),
    "note_list": ("notes", None),
    "citation_list": ("citations", None),
    "media_list": ("media", "ref"),
    "person_ref_list": ("people", "ref"),
    "reporef_list": ("repositories", "ref"),
}

FIELD_LABELS = {
    "gender": "sex",
    "family_list": "families",
    "parent_family_list": "parents' families",
    "child_ref_list": "children",
    "event_ref_list": "events",
    "father_handle": "father",
    "mother_handle": "mother",
    "person_ref_list": "associations",
    "tag_list": "tags",
    "note_list": "notes",
    "citation_list": "citations",
    "media_list": "media",
    "reporef_list": "repositories",
    "alternate_names": "other names",
    "address_list": "addresses",
    "attribute_list": "attributes",
    "urls": "links",
    "primary_name.first_name": "first name",
    "primary_name.surname_list": "surname",
    "primary_name.suffix": "suffix",
    "primary_name.title": "title",
    "gramps_id": "ID",
}

# Fields worth listing when an object is created, in order.
CREATE_FIELDS: dict[str, tuple[str, ...]] = {
    "people": ("gender", "event_ref_list", "family_list", "parent_family_list"),
    "families": ("father_handle", "mother_handle", "child_ref_list", "event_ref_list"),
    "events": ("place",),
    "places": ("type",),
}

IGNORED_FIELDS = frozenset({"change", "birth_ref_index", "death_ref_index"})


def short(value: Any, limit: int = MAX_TEXT) -> str:
    text = value if isinstance(value, str) else str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def date_text(value: Any) -> str:
    if not isinstance(value, dict):
        return short(value) if value else ""
    text = value.get("text")
    if isinstance(text, str) and text:
        return text
    dateval = value.get("dateval")
    if isinstance(dateval, list) and len(dateval) >= 3:
        day, month, year = dateval[0], dateval[1], dateval[2]
        parts = [f"{year:04d}" if year else "", f"{month:02d}" if month else ""]
        if day:
            parts.append(f"{day:02d}")
        return "-".join(p for p in parts if p)
    return ""


def type_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("string") or value.get("value") or "")
    return str(value) if value else ""


def _person_name(obj: JsonObject) -> str:
    profile = obj.get("profile")
    if isinstance(profile, dict) and (
        profile.get("name_given") or profile.get("name_surname")
    ):
        return " ".join(
            p for p in (profile.get("name_given"), profile.get("name_surname")) if p
        )
    name = obj.get("primary_name")
    if isinstance(name, dict):
        surnames = name.get("surname_list") or [{}]
        parts = [name.get("first_name"), surnames[0].get("surname")]
        return " ".join(str(p) for p in parts if p)
    return ""


def _event_text(obj: JsonObject) -> str:
    profile = obj.get("profile")
    profile = profile if isinstance(profile, dict) else {}
    kind = type_text(obj.get("type")) or str(profile.get("type") or "event")
    when = date_text(obj.get("date")) or str(profile.get("date") or "")
    description = obj.get("description")
    if isinstance(description, str) and description.strip():
        return f"{short(description, 50)} ({when})" if when else short(description, 50)
    return f"{kind}, {when}" if when else kind


class Labeler:
    """Names objects: the ones being created, else the ones in the tree."""

    def __init__(
        self,
        client: httpx2.AsyncClient,
        created: Mapping[str, tuple[str, JsonObject]],
    ) -> None:
        self._client = client
        self._created = created
        self._cache: dict[tuple[str, str], str] = {}

    async def label(self, type_: str, handle: str, depth: int = 0) -> str:
        key = (type_, handle)
        if key in self._cache:
            return self._cache[key]
        obj: JsonObject | None = None
        if handle in self._created:
            obj = self._created[handle][1]
        else:
            response = await self._client.get(
                f"/api/{type_}/{handle}", params={"profile": "self"}
            )
            if response.status_code < 400:
                obj = response.json()
        text = await self.name(type_, obj, depth) if obj else ""
        text = text or f"a {SINGULAR.get(type_, type_)} that is not in the tree"
        self._cache[key] = text
        return text

    async def name(self, type_: str, obj: JsonObject, depth: int = 0) -> str:
        profile = obj.get("profile")
        profile = profile if isinstance(profile, dict) else {}
        if type_ == "people":
            return _person_name(obj) or str(obj.get("gramps_id") or "")
        if type_ == "events":
            return _event_text(obj)
        if type_ == "families":
            return await self._family_name(obj, profile, depth)
        if type_ == "places":
            name = obj.get("name")
            value = name.get("value") if isinstance(name, dict) else None
            return str(profile.get("name") or value or obj.get("title") or "")
        if type_ == "notes":
            text = obj.get("text")
            string = text.get("string") if isinstance(text, dict) else ""
            return short(string, 40)
        if type_ == "citations":
            source = profile.get("source")
            title = source.get("title") if isinstance(source, dict) else ""
            return short(obj.get("page") or title or obj.get("gramps_id") or "")
        for key in ("title", "name", "description", "gramps_id"):
            value = obj.get(key)
            if isinstance(value, str) and value:
                return short(value)
        return ""

    async def _family_name(
        self, obj: JsonObject, profile: JsonObject, depth: int
    ) -> str:
        names: list[str] = []
        for role, field in (("father", "father_handle"), ("mother", "mother_handle")):
            person = profile.get(role)
            if isinstance(person, dict) and person:
                names.append(_person_name({"profile": person}))
            elif obj.get(field) and depth < MAX_DEPTH:
                names.append(await self.label("people", str(obj[field]), depth + 1))
        names = [n for n in names if n]
        if names:
            return "family of " + " & ".join(names)
        return "a family"

    async def value(self, field: str, value: Any) -> str:
        reference = REFERENCES.get(field)
        if reference is not None:
            return await self.label(reference[0], _handle(value, reference[1]))
        return scalar_text(field, value)


def _handle(item: Any, key: str | None) -> str:
    if key is not None and isinstance(item, dict):
        return str(item.get(key, ""))
    return str(item)


def scalar_text(field: str, value: Any) -> str:
    if value is None or value == "" or value == []:
        return "nothing"
    if field == "gender" and isinstance(value, int):
        return GENDERS.get(value, str(value))
    if field == "date":
        return date_text(value) or "nothing"
    if field == "type":
        return type_text(value) or "nothing"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dict):
        return short(date_text(value) or value.get("string") or value)
    if isinstance(value, list):
        return ", ".join(short(_plain(v)) for v in value)
    return short(value)


def _plain(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("surname", "string", "title", "text", "value"):
            if isinstance(item.get(key), str) and item[key]:
                return str(item[key])
    return str(item)


def field_label(path: str) -> str:
    return FIELD_LABELS.get(path) or path.replace("_", " ").replace(".", " › ")


def diff_paths(old: Any, new: Any, prefix: str = "") -> list[tuple[str, Any, Any]]:
    """Changed leaves; a date is one change, and lists are compared whole."""
    if isinstance(old, dict) and isinstance(new, dict):
        out: list[tuple[str, Any, Any]] = []
        for key in sorted(set(old) | set(new)):
            path = f"{prefix}.{key}" if prefix else key
            if key in IGNORED_FIELDS and not prefix:
                continue
            if (path == "date" or key == "date") and old.get(key) != new.get(key):
                out.append((path, old.get(key), new.get(key)))
            else:
                out.extend(diff_paths(old.get(key), new.get(key), path))
        return out
    return [] if old == new else [(prefix, old, new)]


@dataclass(frozen=True)
class Detail:
    """One line under a change: an addition, a removal, a value change or a fact."""

    label: str
    kind: Literal["add", "remove", "change", "info"]
    text: str = ""
    before: str = ""
    after: str = ""

    def line(self) -> str:
        if self.kind == "add":
            return f"{self.label}: add {self.text}"
        if self.kind == "remove":
            return f"{self.label}: remove {self.text}"
        if self.kind == "change":
            return f"{self.label}: {self.before} → {self.after}"
        return f"{self.label}: {self.text}"


@dataclass(frozen=True)
class ChangeView:
    """What one operation does, in words, for the text and the card."""

    op: Literal["create", "update", "delete"]
    type: str
    title: str
    details: tuple[Detail, ...] = ()

    @property
    def verb(self) -> str:
        return {"create": "Add", "update": "Edit", "delete": "Delete"}[self.op]

    @property
    def noun(self) -> str:
        return SINGULAR.get(self.type, self.type)

    def lines(self) -> list[str]:
        return [
            f"{self.verb} {self.noun}: {self.title}",
            *(d.line() for d in self.details),
        ]


async def list_change_details(
    labeler: Labeler, field: str, old: Any, new: Any
) -> list[Detail]:
    reference = REFERENCES.get(field)
    old_items: list[Any] = old if isinstance(old, list) else []
    new_items: list[Any] = new if isinstance(new, list) else []

    def key(item: Any) -> str:
        return _handle(item, reference[1]) if reference else str(_plain(item))

    old_keys, new_keys = [key(i) for i in old_items], [key(i) for i in new_items]
    label = field_label(field)
    details: list[Detail] = []
    for item in new_items:
        if key(item) not in old_keys:
            details.append(Detail(label, "add", await labeler.value(field, item)))
    for item in old_items:
        if key(item) not in new_keys:
            details.append(Detail(label, "remove", await labeler.value(field, item)))
    return details or [Detail(label, "info", "reordered")]


async def update_details(
    labeler: Labeler, current: JsonObject, new: JsonObject
) -> list[Detail]:
    details: list[Detail] = []
    for path, old, value in diff_paths(current, new):
        if isinstance(old, list) or isinstance(value, list):
            details.extend(await list_change_details(labeler, path, old, value))
        else:
            before = await labeler.value(path, old)
            after = await labeler.value(path, value)
            details.append(
                Detail(field_label(path), "change", before=before, after=after)
            )
    return details


async def create_details(
    labeler: Labeler, type_: str, body: JsonObject
) -> list[Detail]:
    details: list[Detail] = []
    for field in CREATE_FIELDS.get(type_, ()):
        value = body.get(field)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            texts = [await labeler.value(field, item) for item in value]
            details.append(Detail(field_label(field), "info", ", ".join(texts)))
        else:
            details.append(
                Detail(field_label(field), "info", await labeler.value(field, value))
            )
    return details


async def view_operation(
    labeler: Labeler,
    op: Literal["create", "update", "delete"],
    type_: str,
    body: JsonObject | None,
    current: JsonObject | None,
) -> ChangeView:
    if op == "create":
        obj = body or {}
        title = await labeler.name(type_, obj) or "(no name)"
        return ChangeView(
            op, type_, title, tuple(await create_details(labeler, type_, obj))
        )
    obj = current or {}
    title = await labeler.name(type_, obj) or str(obj.get("gramps_id") or "?")
    ident = obj.get("gramps_id")
    if ident and str(ident) not in title:
        title = f"{title} ({ident})"
    if op == "delete":
        return ChangeView(op, type_, title)
    return ChangeView(
        op, type_, title, tuple(await update_details(labeler, obj, body or {}))
    )
