from __future__ import annotations

import pytest

from gramps_mcp.selection import (
    EditError,
    check_edit,
    closure,
    direct_requirements,
    edit_fields,
    get_path,
    missing_requirements,
    set_path,
)

PERSON = {
    "handle": "p7",
    "primary_name": {"first_name": "Ileana", "surname_list": [{"surname": "Viveret"}]},
    "event_ref_list": [{"ref": "e6"}],
}

# The scenario of the screenshot: event, person (needs the event), family
# (needs the person), and the father's edit (needs the family).
SCENARIO = [
    ("create", {"handle": "e6", "type": "Birth"}),
    ("create", PERSON),
    (
        "create",
        {"handle": "f8", "father_handle": "jer", "child_ref_list": [{"ref": "p7"}]},
    ),
    ("update", {"handle": "jer", "family_list": ["f8"]}),
    ("delete", None),
]


def test_a_change_needs_the_changes_that_create_what_it_mentions():
    assert direct_requirements(SCENARIO) == [set(), {0}, {1}, {2}, set()]


def test_dependencies_are_followed_all_the_way():
    requires, required_by = closure(direct_requirements(SCENARIO))
    assert requires == [set(), {0}, {0, 1}, {0, 1, 2}, set()]
    assert required_by == [{1, 2, 3}, {2, 3}, {3}, set(), set()]


def test_a_change_never_depends_on_itself_or_on_existing_objects():
    ops = [("create", {"handle": "a", "father_handle": "existing", "self": "a"})]
    assert direct_requirements(ops) == [set()]


def test_dependencies_must_not_loop_forever():
    ops = [
        ("create", {"handle": "a", "ref": "b"}),
        ("create", {"handle": "b", "ref": "a"}),
    ]
    requires, required_by = closure(direct_requirements(ops))
    assert requires == [{1}, {0}] and required_by == [{1}, {0}]


def test_missing_requirements_names_each_gap():
    requires, _ = closure(direct_requirements(SCENARIO))
    assert missing_requirements({0, 1, 2, 3, 4}, requires) == []
    assert missing_requirements({2, 4}, requires) == [(2, 0), (2, 1)]
    assert missing_requirements(set(), requires) == []


def test_paths_are_read_and_written_through_dicts_and_lists():
    obj = {"a": {"b": [{"c": "x"}]}}
    assert get_path(obj, "a.b.0.c") == "x"
    assert get_path(obj, "a.b.5.c") is None
    set_path(obj, "a.b.0.c", "y")
    assert obj["a"]["b"][0]["c"] == "y"
    with pytest.raises(EditError):
        set_path(obj, "a.z.0.c", "y")


def test_a_created_object_offers_its_text_fields_and_a_deleted_one_none():
    ops = [
        ("create", "people", PERSON, None),
        ("create", "families", {"handle": "f8"}, None),
        ("delete", "people", None, {"handle": "x"}),
    ]
    fields = edit_fields(ops)
    assert [(f.key, f.op, f.path, f.value) for f in fields] == [
        ("ed0", 0, "primary_name.first_name", "Ileana"),
        ("ed1", 0, "primary_name.surname_list.0.surname", "Viveret"),
    ]


def test_an_edited_object_offers_only_the_fields_its_change_touches():
    current = {
        "primary_name": {"first_name": "Marie", "surname_list": [{"surname": "Dupont"}]}
    }
    body = {
        "primary_name": {"first_name": "Mari", "surname_list": [{"surname": "Dupont"}]}
    }
    fields = edit_fields([("update", "people", body, current)])
    assert [(f.path, f.value) for f in fields] == [("primary_name.first_name", "Mari")]
    assert edit_fields([("update", "people", current, current)]) == []


@pytest.mark.parametrize("bad", [None, 3, "x" * 501, "a\x00b", "tab\x1fchar"])
def test_edits_are_checked(bad):
    with pytest.raises(EditError):
        check_edit(bad)  # type: ignore[arg-type]


def test_a_good_edit_is_trimmed_and_may_have_accents_and_newlines_in_text():
    assert check_edit("  Jérôme Benoît ") == "Jérôme Benoît"
    assert check_edit("line one\nline two") == "line one\nline two"
