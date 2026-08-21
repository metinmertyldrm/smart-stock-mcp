"""Compatibility facade for the Smart Stock agent runtime.

The historical runtime implementation lives in :mod:`agent_runtime` while
focused, deterministic concerns are extracted into dedicated modules. Existing
imports from ``app`` remain stable during the refactor.
"""
import asyncio

import agent_runtime as _runtime
import plan_execution as _execution
from agent_runtime import *  # noqa: F401,F403
from plan_execution import (  # noqa: F401
    COLLECTION_ARGUMENTS,
    EMPTY_SOURCE_REASONS,
    TARGET_ACTIONS,
    TRANSFORMS,
    TRANSFORM_INPUT_TYPES,
    detect_empty_input,
    execute_plan,
    extract_result_text,
    filter_list_by_query,
    get_nested_value,
    get_product_id,
    get_product_name,
    get_replenishment_quantity,
    item_matches_query,
    low_stock_products_to_items,
    normalize_tool_result,
    order_to_incoming_items,
    out_of_stock_products_to_items,
    plan_to_draft_items,
    remove_none_values,
    replenishments_to_items,
    resolve_argument_value,
    resolve_context_path,
    resolve_context_reference,
    resolve_step_arguments,
    save_reference,
    serialize_plan,
    update_last_product,
)
from plan_validation import (  # noqa: F401
    ALLOWED_CONTEXT_SOURCES,
    INFO_TOOLS,
    WRITE_TOOLS,
    parse_execution_plan,
    remove_json_comments,
    validate_plan_against_state,
)

# Functions imported from agent_runtime keep that module's global namespace.
# Rebind those globals to the extracted implementations so CLI, web and tests
# share one validation/execution path while the legacy runtime is decomposed.
_runtime.ALLOWED_CONTEXT_SOURCES = ALLOWED_CONTEXT_SOURCES
_runtime.INFO_TOOLS = INFO_TOOLS
_runtime.parse_execution_plan = parse_execution_plan
_runtime.remove_json_comments = remove_json_comments
_runtime.validate_plan_against_state = validate_plan_against_state

_EXECUTION_EXPORTS = (
    "COLLECTION_ARGUMENTS",
    "EMPTY_SOURCE_REASONS",
    "TARGET_ACTIONS",
    "TRANSFORMS",
    "TRANSFORM_INPUT_TYPES",
    "detect_empty_input",
    "execute_plan",
    "extract_result_text",
    "filter_list_by_query",
    "get_nested_value",
    "get_product_id",
    "get_product_name",
    "get_replenishment_quantity",
    "item_matches_query",
    "low_stock_products_to_items",
    "normalize_tool_result",
    "order_to_incoming_items",
    "out_of_stock_products_to_items",
    "plan_to_draft_items",
    "remove_none_values",
    "replenishments_to_items",
    "resolve_argument_value",
    "resolve_context_path",
    "resolve_context_reference",
    "resolve_step_arguments",
    "save_reference",
    "serialize_plan",
    "update_last_product",
)
for _name in _EXECUTION_EXPORTS:
    setattr(_runtime, _name, getattr(_execution, _name))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nKapatılıyor...")
