from __future__ import annotations

from typing import Any

import httpx2
from pydantic import TypeAdapter

from gramps_mcp.changes import Operations, format_confirmation, plan
from gramps_mcp.summary import diff_paths, scalar_text

OPERATIONS = TypeAdapter(Operations)

JEROME = {
    "handle": "jer",
    "gramps_id": "I0001",
    "gender": 1,
    "primary_name": {
        "first_name": "Jérôme Benoît",
        "surname_list": [{"surname": "Viveret"}],
    },
    "family_list": [],
    "profile": {"name_given": "Jérôme Benoît", "name_surname": "Viveret"},
}


def client_for(objects: dict[str, dict[str, Any]]) -> httpx2.AsyncClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        handle = request.url.path.rstrip("/").split("/")[-1]
        if handle in objects:
            return httpx2.Response(200, json=objects[handle])
        return httpx2.Response(404, json={"error": {"message": "nope"}})

    return httpx2.AsyncClient(
        base_url="http://gramps", transport=httpx2.MockTransport(handler)
    )


async def text_of(operations: list[dict[str, Any]], objects: dict[str, Any]) -> str:
    planned = await plan(client_for(objects), OPERATIONS.validate_python(operations))
    return format_confirmation(planned)


async def test_a_new_child_reads_as_names_not_handles():
    text = await text_of(
        [
            {
                "op": "create",
                "type": "events",
                "object": {
                    "handle": "e6",
                    "type": "Birth",
                    "date": {"text": "10 août 2010", "dateval": [10, 8, 2010, False]},
                },
            },
            {
                "op": "create",
                "type": "people",
                "object": {
                    "handle": "p7",
                    "gender": 0,
                    "primary_name": {
                        "first_name": "Ileana",
                        "surname_list": [{"surname": "Viveret"}],
                    },
                    "event_ref_list": [{"ref": "e6", "role": "Primary"}],
                },
            },
            {
                "op": "create",
                "type": "families",
                "object": {
                    "handle": "f8",
                    "father_handle": "jer",
                    "child_ref_list": [{"ref": "p7"}],
                },
            },
            {
                "op": "update",
                "type": "people",
                "handle": "jer",
                "changes": {"family_list": ["f8"]},
            },
        ],
        {"jer": JEROME},
    )
    assert text == "\n".join(
        [
            "Apply these 4 change(s) to the tree?",
            "",
            "1. Add event: Birth, 10 août 2010",
            "2. Add person: Ileana Viveret",
            "    - sex: female",
            "    - events: Birth, 10 août 2010",
            "3. Add family: family of Jérôme Benoît Viveret",
            "    - father: Jérôme Benoît Viveret",
            "    - children: Ileana Viveret",
            "4. Edit person: Jérôme Benoît Viveret (I0001)",
            "    - families: add family of Jérôme Benoît Viveret",
        ]
    )
    assert "f8" not in text and "p7" not in text and "_list" not in text


async def test_existing_objects_are_named_through_the_profile():
    family = {
        "handle": "fam",
        "gramps_id": "F0732",
        "father_handle": "jer",
        "child_ref_list": [],
    }
    text = await text_of(
        [
            {
                "op": "update",
                "type": "families",
                "handle": "fam",
                "changes": {"child_ref_list": [{"ref": "jer", "frel": "Birth"}]},
            }
        ],
        {"fam": family, "jer": JEROME},
    )
    assert "Edit family: family of Jérôme Benoît Viveret (F0732)" in text
    assert "children: add Jérôme Benoît Viveret" in text


async def test_scalar_changes_use_words():
    person = {**JEROME, "date": {"text": "1824", "dateval": [0, 0, 1824, False]}}
    text = await text_of(
        [
            {
                "op": "update",
                "type": "people",
                "handle": "jer",
                "changes": {
                    "gender": 0,
                    "date": {
                        "text": "1830",
                        "dateval": [0, 0, 1830, False],
                        "year": 1830,
                    },
                },
            }
        ],
        {"jer": person},
    )
    assert "sex: male → female" in text
    assert text.count("date:") == 1 and "date: 1824 → 1830" in text


async def test_unknown_references_do_not_show_raw_handles():
    text = await text_of(
        [
            {
                "op": "update",
                "type": "people",
                "handle": "jer",
                "changes": {"family_list": ["gone"]},
            }
        ],
        {"jer": JEROME},
    )
    assert "add a family that is not in the tree" in text
    assert "gone" not in text


async def test_delete_is_named():
    text = await text_of(
        [{"op": "delete", "type": "people", "handle": "jer"}], {"jer": JEROME}
    )
    assert "Delete person: Jérôme Benoît Viveret (I0001)" in text


def test_scalar_text_and_diff_paths():
    assert scalar_text("gender", 3) == "other"
    assert scalar_text("private", True) == "yes"
    assert scalar_text("description", "") == "nothing"
    assert diff_paths({"a": {"b": 1}, "change": 1}, {"a": {"b": 2}, "change": 2}) == [
        ("a.b", 1, 2)
    ]


async def test_an_event_with_a_description_is_named_by_it():
    event = {
        "handle": "ev",
        "gramps_id": "E1",
        "type": "Birth",
        "description": "Naissance de Ileana Viveret",
        "date": {"text": "10 août 2010", "dateval": [10, 8, 2010, False]},
    }
    text = await text_of(
        [{"op": "delete", "type": "events", "handle": "ev"}], {"ev": event}
    )
    assert "Delete event: Naissance de Ileana Viveret (10 août 2010) (E1)" in text


def client_with_lists(objects: dict[str, dict[str, Any]]) -> httpx2.AsyncClient:
    """Like Gramps: a collection path returns a list, whatever its parameters."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        parts = request.url.path.strip("/").split("/")
        if len(parts) == 2:
            return httpx2.Response(200, json=list(objects.values()))
        handle = parts[-1]
        if handle in objects:
            return httpx2.Response(200, json=objects[handle])
        return httpx2.Response(404, json={"error": {"message": "nope"}})

    return httpx2.AsyncClient(
        base_url="http://gramps", transport=httpx2.MockTransport(handler)
    )


async def summary_with_lists(
    operations: list[dict[str, Any]], objects: dict[str, Any]
) -> str:
    planned = await plan(
        client_with_lists(objects), OPERATIONS.validate_python(operations)
    )
    return format_confirmation(planned)


async def test_an_empty_place_does_not_ask_for_the_list_of_places():
    # Gramps answers /api/places/ with a list: an event created with `place: ""`
    # used to crash the summary ('list' object has no attribute 'get')
    text = await summary_with_lists(
        [
            {
                "op": "create",
                "type": "events",
                "object": {"type": "Marriage", "place": "", "description": "Mariage"},
            }
        ],
        {},
    )
    assert "Add event" in text


async def test_clearing_a_place_says_so():
    event = {"handle": "e1", "type": "Marriage", "place": "pl1", "description": "x"}
    place = {"handle": "pl1", "name": {"value": "São Paulo, Brésil"}}
    text = await summary_with_lists(
        [{"op": "update", "type": "events", "handle": "e1", "changes": {"place": ""}}],
        {"e1": event, "pl1": place},
    )
    assert "São Paulo, Brésil" in text and "nothing" in text


async def test_an_event_in_a_place_created_in_the_same_set_names_the_place():
    text = await summary_with_lists(
        [
            {
                "op": "create",
                "type": "places",
                "object": {"handle": "sp", "name": {"value": "São Paulo, Brésil"}},
            },
            {
                "op": "create",
                "type": "events",
                "object": {"type": "Marriage", "place": "sp"},
            },
        ],
        {},
    )
    assert "place: São Paulo, Brésil" in text
