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
    outline,
    set_path,
)

PERSON = {
    "handle": "p7",
    "primary_name": {"first_name": "Ileana", "surname_list": [{"surname": "Viveret"}]},
    "event_ref_list": [{"ref": "e6"}],
}

# The scenario of the screenshot: an event that belongs to the person, the person,
# a family (that only lists the person as a child), and the father's edit, which
# only lists the family.
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

# A mother, her child and their family, as Gramps stores them: each side lists
# the other.
MOTHER = {"handle": "m", "family_list": ["f"], "primary_name": {"first_name": "Ana"}}
CHILD = {"handle": "c", "parent_family_list": ["f"], "primary_name": {}}
FAMILY = {"handle": "f", "mother_handle": "m", "child_ref_list": [{"ref": "c"}]}
FAMILY_OF_TWO = [("create", MOTHER), ("create", CHILD), ("create", FAMILY)]

# Three levels: a person, their birth, and a note on the birth.
THREE_LEVELS = [
    ("create", {"handle": "n", "text": {"string": "seen on the register"}}),
    ("create", {"handle": "b", "type": "Birth", "note_list": ["n"]}),
    ("create", {"handle": "p", "event_ref_list": [{"ref": "b", "role": "Primary"}]}),
]


def test_what_a_body_lists_is_owned_what_it_mentions_is_needed():
    # the birth belongs to the person; the family only lists its child
    assert direct_requirements(SCENARIO) == [{1}, set(), set(), set(), set()]


def test_dependencies_are_followed_all_the_way():
    requires, required_by = closure(direct_requirements(THREE_LEVELS))
    assert requires == [{1, 2}, {2}, set()]  # the note needs the birth and the person
    assert required_by == [set(), {0}, {0, 1}]


def test_unticking_the_mother_drops_the_family_and_the_child_not_the_reverse():
    requires, required_by = closure(direct_requirements(FAMILY_OF_TWO))
    # the family needs its mother; the child needs its family, so the mother
    assert requires == [set(), {0, 2}, {0}]
    assert required_by[0] == {1, 2}  # untick the mother: family and child go
    assert required_by[1] == set()  # untick the child: nothing else goes
    assert required_by[2] == {1}


def test_a_child_left_out_is_dropped_from_its_family():
    bodies = drop_unselected_links(FAMILY_OF_TWO, {0, 2})
    assert bodies[2] == {"handle": "f", "mother_handle": "m", "child_ref_list": []}
    assert bodies[0] is MOTHER and FAMILY["child_ref_list"] == [{"ref": "c"}]


def test_a_family_left_out_is_dropped_from_the_mother():
    bodies = drop_unselected_links(FAMILY_OF_TWO, {0})
    assert bodies[0] == {**MOTHER, "family_list": []}


def test_a_note_left_out_is_dropped_from_its_event_at_any_depth():
    ops = [
        ("create", {"handle": "n"}),
        (
            "update",
            {
                "handle": "jer",
                "event_ref_list": [{"ref": "x", "note_list": ["n"], "role": "Primary"}],
            },
        ),
    ]
    bodies = drop_unselected_links(ops, {1})
    assert bodies[1] == {
        "handle": "jer",
        "event_ref_list": [{"ref": "x", "note_list": [], "role": "Primary"}],
    }


def test_nothing_is_dropped_when_everything_is_selected():
    bodies = drop_unselected_links(FAMILY_OF_TWO, {0, 1, 2})
    assert all(b is o[1] for b, o in zip(bodies, FAMILY_OF_TWO, strict=True))


def test_a_change_never_depends_on_itself_or_on_existing_objects():
    ops = [("create", {"handle": "a", "father_handle": "x", "self": "a"})]
    assert direct_requirements(ops) == [set()]


def test_dependencies_must_not_loop_forever():
    ops = [
        ("create", {"handle": "a", "father_handle": "b"}),
        ("create", {"handle": "b", "father_handle": "a"}),
    ]
    requires, required_by = closure(direct_requirements(ops))
    assert requires == [{1}, {0}] and required_by == [{1}, {0}]
    assert outline(direct_requirements(ops)) == [(0, [(1, [])])]


def test_the_outline_is_a_tree_with_what_is_needed_first():
    assert outline(direct_requirements(THREE_LEVELS)) == [
        (2, [(1, [(0, [])])])  # person > birth > note
    ]
    assert outline(direct_requirements(FAMILY_OF_TWO)) == [
        (0, [(2, [(1, [])])])  # mother > family > child
    ]
    # siblings keep the order the changes had
    assert outline(direct_requirements(SCENARIO)) == [
        (1, [(0, [])]),
        (2, []),
        (3, []),
        (4, []),
    ]


def test_missing_requirements_names_each_gap():
    requires, _ = closure(direct_requirements(THREE_LEVELS))
    assert missing_requirements({0, 1, 2}, requires) == []
    assert missing_requirements({0, 2}, requires) == [(0, 1)]
    assert missing_requirements({0}, requires) == [(0, 1), (0, 2)]
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


def test_a_birth_added_to_an_existing_person_belongs_to_that_edit():
    ops = [
        ("create", {"handle": "b", "type": "Birth"}),
        ("update", {"handle": "jer", "event_ref_list": [{"ref": "b"}]}),
    ]
    requires, required_by = closure(direct_requirements(ops))
    assert requires == [{1}, set()]  # untick the edit: the birth goes
    assert required_by == [set(), {0}]  # untick the birth: the edit stays
    assert drop_unselected_links(ops, {1})[1] == {"handle": "jer", "event_ref_list": []}


# A person, their birth, and the place of the birth, all new.
WITH_PLACE = [
    ("create", {"handle": "p", "event_ref_list": [{"ref": "b"}]}),
    ("create", {"handle": "b", "type": "Birth", "place": "sp"}),
    ("create", {"handle": "sp", "name": {"value": "São Paulo, Brésil"}}),
]


def test_a_place_made_for_one_event_hangs_under_it_and_goes_with_it():
    direct = direct_requirements(WITH_PLACE)
    assert direct == [set(), {0}, {1}]
    assert outline(direct) == [(0, [(1, [(2, [])])])]  # person > birth > place
    requires, required_by = closure(direct)
    assert required_by[1] == {2}  # untick the birth: its place goes
    assert required_by[2] == set()  # untick the place: the birth stays


def test_a_place_that_two_events_use_stands_alone():
    ops = [
        ("create", {"handle": "p", "event_ref_list": [{"ref": "b"}, {"ref": "d"}]}),
        ("create", {"handle": "b", "type": "Birth", "place": "sp"}),
        ("create", {"handle": "d", "type": "Death", "place": "sp"}),
        ("create", {"handle": "sp", "name": {"value": "São Paulo, Brésil"}}),
    ]
    direct = direct_requirements(ops)
    assert direct[3] == set()
    assert outline(direct) == [(0, [(1, []), (2, [])]), (3, [])]
    # unticking the place leaves both events, without a place
    bodies = drop_unselected_links(ops, {0, 1, 2})
    assert bodies[1] == {"handle": "b", "type": "Birth", "place": ""}
    assert bodies[2] == {"handle": "d", "type": "Death", "place": ""}


def test_a_place_that_an_owner_lists_is_not_taken_for_its_user():
    # a note belongs to the person that lists it, whatever else mentions it
    ops = [
        ("create", {"handle": "p", "note_list": ["n"]}),
        ("create", {"handle": "n", "text": {}}),
    ]
    assert direct_requirements(ops) == [set(), {0}]
