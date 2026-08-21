"""Compatibility facade for the Smart Stock agent runtime.

The historical implementation lives in :mod:`agent_runtime` while focused,
pure concerns are extracted into dedicated modules. Existing imports from
``app`` remain stable during the refactor.
"""
import asyncio

from agent_runtime import *  # noqa: F401,F403
from plan_validation import (  # noqa: F401
    ALLOWED_CONTEXT_SOURCES,
    INFO_TOOLS,
    WRITE_TOOLS,
    parse_execution_plan,
    remove_json_comments,
    validate_plan_against_state,
)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nKapatılıyor...")
