"""Unofficial MCP server exposing a Gramps Web API instance to LLM assistants.

The names re-exported here are the supported way to write to a Gramps tree from
Python without going through MCP: validate a set of operations, plan them
against the current objects, apply them in order, and undo them if one fails.
They are what `apply_changes` itself is built on. See
docs/reference/python-api.md; anything not listed in `__all__` is internal and
may change without notice.
"""

from .changes import (
    MAX_OPERATIONS,
    ApplyChangesResult,
    ChangeError,
    ChangeSummary,
    CreateOperation,
    DeleteOperation,
    Failure,
    Operations,
    Planned,
    UpdateOperation,
    execute,
    format_confirmation,
    plan,
    rollback,
)

__version__ = "0.1.0"

__all__ = [
    "MAX_OPERATIONS",
    "ApplyChangesResult",
    "ChangeError",
    "ChangeSummary",
    "CreateOperation",
    "DeleteOperation",
    "Failure",
    "Operations",
    "Planned",
    "UpdateOperation",
    "__version__",
    "execute",
    "format_confirmation",
    "plan",
    "rollback",
]
