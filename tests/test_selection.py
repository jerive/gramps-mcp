from __future__ import annotations

import pytest

from gramps_mcp.selection import (
    EditError,
    check_edit,
    closure,
    direct_requirements,
    drop_unselected_links,
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
# (lists the person as a child), and the father's edit (needs the family).
SCENARIO = [
    ("create", "events", {"handle": "e6", "type": "Birth"}),
    ("create", "people", PERSON),
    (
        "create",
        "families",
        {"handle": "f8", "father_handle": "jer", "child_ref_list": [{"ref": "p7"}]},
    ),
    ("update", "people", {"handle": "jer", "family_list": ["f8"]}),
    ("delete", "people", None),
]

# A mother, her child and their family, as Gramps stores them: each side lists
# the other.
MOTHER = {"handle": "m", "family_list": ["f"], "primary_name": {"first_name": "Ana"}}
CHILD = {"handle": "c", "parent_family_list": ["f"], "primary_name": {}}
FAMILY = {"handle": "f", "mother_handle": "m", "child_ref_list": [{"ref": "c"}]}
FAMILY_OF_TWO = [
    ("create", "people", MOTHER),
    ("create", "people", CHILD),
    ("create", "families", FAMILY),
]


def test_a_change_needs_the_changes_that_create_what_it_mentions():
    # the family only lists its child, which is not a need
    assert direct_requirements(SCENARIO) == [set(), {0}, set(), {2}, set()]


def test_dependencies_are_followed_all_the_way():
    requires, required_by = closure(direct_requirements(SCENARIO))
    assert requires == [set(), {0}, set(), {2}, set()]
    assert required_by == [{1}, set(), {3}, set(), set()]


def test_unticking_the_mother_drops_the_family_and_the_child_not_the_reverse():
    requires, required_by = closure(direct_requirements(FAMILY_OF_TWO))
    # the family needs its mother; the child needs its family, so the mother
    assert requires == [set(), {0, 2}, {0}]
    assert required_by[0] == {1, 2}  # untick the mother: family and child go
    assert required_by[1] == set()  # untick the child: nothing else goes
    assert required_by[2] == {1}


def test_a_child_left_out_is_dropped_from_its_family():
    ops = [(op, type_, body) for op, type_, body in FAMILY_OF_TWO]
    bodies = drop_unselected_links(ops, {0, 2})
    assert bodies[2] == {"handle": "f", "mother_handle": "m", "child_ref_list": []}
    assert bodies[0] is MOTHER and ops[2][2] == FAMILY  # the input is untouched


def test_a_family_left_out_is_dropped_from_the_mother():
    bodies = drop_unselected_links(FAMILY_OF_TWO, {0})
    assert bodies[0] == {**MOTHER, "family_list": []}


def test_nothing_is_dropped_when_everything_is_selected():
    bodies = drop_unselected_links(FAMILY_OF_TWO, {0, 1, 2})
    assert all(b is o[2] for b, o in zip(bodies, FAMILY_OF_TWO, strict=True))


def test_a_change_never_depends_on_itself_or_on_existing_objects():
    ops = [("create", "people", {"handle": "a", "father_handle": "x", "self": "a"})]
    assert direct_requirements(ops) == [set()]


def test_dependencies_must_not_loop_forever():
    ops = [
        ("create", "events", {"handle": "a", "ref": "b"}),
        ("create", "events", {"handle": "b", "ref": "a"}),
    ]
    requires, required_by = closure(direct_requirements(ops))
    assert requires == [{1}, {0}] and required_by == [{1}, {0}]


def test_missing_requirements_names_each_gap():
    requires, _ = closure(direct_requirements(SCENARIO))
    assert missing_requirements({0, 1, 2, 3, 4}, requires) == []
    assert missing_requirements({1, 3, 4}, requires) == [(1, 0), (3, 2)]
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
