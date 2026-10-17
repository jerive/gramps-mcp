# Querying a tree with GOQL

GOQL, the Gramps Object Query Language, is the filter language of Gramps Web
API's structured search. It reads like one Python boolean expression and is
written against one object type at a time (people, families, events, places,
citations, sources, repositories, media, notes). It is a feature of
[Gramps Web API](https://github.com/gramps-project/gramps-web-api) and its
[gramps-object-query-language](https://github.com/dsblank/gramps-object-query-language)
library, not something this project defines. The full reference is in that
library's `docs/where_expr.md`; this page covers what an agent needs most.

Requires Gramps Web API 3.21 or newer for the `query` tools, and 3.23 or newer
for the `MatchesQuery` filter rule.

## Two ways to use it

**1. The `query_objects` tool** (with an `object_type` such as `people`) takes a
JSON body. This is the better choice: it can return only the fields you ask
for, sort, page and count.

```json
{
  "where_expr": "gender == Person.FEMALE and like(birth.place.title, '%TX%')",
  "select": ["gramps_id", "primary_name.first_name", "birth.place.title as place"],
  "order_by": [{"column": "birth.date.sortval", "direction": "asc"}],
  "limit": 20,
  "count": true
}
```

| Field | Meaning |
|---|---|
| `where_expr` | The GOQL expression, as a string. |
| `select` | Values to return. Defaults to everything, which is large: always select. |
| `order_by` | List of `{"column": ..., "direction": "asc" or "desc"}`. |
| `limit` | Rows per page, 1 to 1000, default 50. |
| `after` | The `next_after` cursor of the previous page, for the next one. |
| `count` | If true, the total number of matches is returned in the `X-Total-Count` header. |

The response has `items` and, when there are more rows, `next_after`.

**2. The `rules` parameter** of `list_objects` accepts the same expression through the `MatchesQuery` rule:

```json
{"rules": [{"name": "MatchesQuery", "values": ["gender == Person.MALE"]}]}
```

## Writing expressions

A field is a dotted path, with `[n]` to pick a list element:
`gender`, `primary_name.surname_list[0].surname`, `birth.date.sortval`.

| To say | Write |
|---|---|
| equals, differs | `==`, `!=` |
| order | `<`, `<=`, `>`, `>=` |
| and, or, not | `and`, `or`, `not (...)` |
| one of a list | `primary_name.first_name in ['John', 'Mary']` |
| contains text | `'Ann' in primary_name.first_name` |
| pattern, case-insensitive | `like(field, 'Gar%')` (`%` is any text, `_` one character) |
| regular expression | `regex(primary_name.first_name, '^Ann')` |
| a date | `Date('Jan 1, 1968')`, compared with `.sortval` |

Fixed values have names: `Person.MALE`, `Person.FEMALE`, `EventType.BIRTH`,
`FamilyRelType.MARRIED`, and so on. Types such as an event's `type` store
their number in `.value`, so write `type.value == EventType.BIRTH`.

### Crossing relationships

A path can continue into a related object:

| From | Name | Goes to |
|---|---|---|
| Person | `birth`, `death` | their birth or death event |
| Family | `father`, `mother` | the parent |
| Event | `place` | the place |
| Citation | `source` | the source |
| Place | `enclosed_by` | the enclosing place |

So `birth.place.title` is a person's birth place, and paths chain freely
(`father.birth.date.sortval`). Two paths can be compared to each other.

### Families and their children

`any(children)` is true for a family with at least one child;
`any(children, gender == Person.FEMALE)` for one with a daughter;
`len(children) >= 4` counts them.

## Examples

These all ran against the Gramps example tree.

| Question | Type | `where_expr` |
|---|---|---|
| Men | people | `gender == Person.MALE` |
| Surnames starting with Gar | people | `like(primary_name.surname_list[0].surname, 'Gar%')` |
| Born in 1968 or later | people | `birth.date.sortval >= Date('Jan 1, 1968')` |
| Born in the 1900s | people | `birth.date.sortval >= Date('Jan 1, 1900') and birth.date.sortval < Date('Jan 1, 1910')` |
| Women born in Texas | people | `like(birth.place.title, '%TX%') and gender == Person.FEMALE` |
| Mother died before father | families | `mother.death.date.sortval < father.death.date.sortval` |
| Families with 4 children or more | families | `len(children) >= 4` |
| Birth events | events | `type.value == EventType.BIRTH` |

## Things to know

- **Do not guess field names.** An unknown field is rejected with a 422 whose
  message lists the fields that exist for that type. Read it and retry.
- **Select what you need.** `select` accepts paths and renames with `as`:
  `"birth.date.dateval as born"`.
- **Sorting by a path** such as `birth.date.sortval` puts people without that
  value first when ascending.
- **Ask for the total** with `count: true` rather than paging through
  everything to count it.
- `sortval` is a plain number that orders dates. It ignores qualifiers such as
  "about" or "before", so it cannot tell "about 1900" from "1900".
