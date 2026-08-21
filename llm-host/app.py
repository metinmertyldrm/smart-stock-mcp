"""Compatibility facade for the Smart Stock agent runtime.

The historical runtime implementation lives in :mod:`agent_runtime` while
focused, pure concerns are extracted into dedicated modules. Existing imports
from ``app`` remain stable during the refactor.
"""
import asyncio

import agent_runtime as _runtime
from agent_runtime import *  # noqa: F401,F403
from plan_validation import (  # noqa: F401
    ALLOWED_CONTEXT_SOURCES,
    INFO_TOOLS,
    WRITE_TOOLS,
    parse_execution_plan,
    remove_json_comments,
    validate_plan_against_state,
)

# Functions imported from agent_runtime keep that module's global namespace.
# Bind those globals to the extracted implementation as well so the CLI path,
# reference resolution, and the web compatibility facade share one safety API.
_runtime.ALLOWED_CONTEXT_SOURCES = ALLOWED_CONTEXT_SOURCES
_runtime.INFO_TOOLS = INFO_TOOLS
_runtime.parse_execution_plan = parse_execution_plan
_runtime.remove_json_comments = remove_json_comments
_runtime.validate_plan_against_state = validate_plan_against_state


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nKapatılıyor...")
