"""Schema extraction helpers for capability registry entries.

Generated capabilities should enter the registry with meaningful
``input_schema`` and ``output_schema`` fields because optimizer merge and
reuse decisions compare structural compatibility, not just prose
descriptions. Generators normally attach schemas from the decomposed
``SubTask``. These helpers provide registration-time fallbacks:

1. ``extract_mcp_tool_signature`` parses generated MCP tool source with
   ``ast`` and recovers parameter types plus literal return-dict keys.
2. ``find_subtask_for_gap`` matches a generated artifact's gap query to
   the decomposed subtask that produced it.
3. ``derive_input_schema_from_upstream`` derives SKILL / AGENT input
   contracts from predecessor subtasks in a compositional chain.
"""

from __future__ import annotations

import ast
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.schemas.task import SubTask

logger = logging.getLogger(__name__)


def extract_mcp_tool_signature(
    source_code: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Parse MCP tool source and return (input_schema, output_schema).

    Walks the AST looking for the first ``async def`` function decorated
    with ``@mcp.tool(...)`` (or ``@mcp.tool``). Extracts parameter names
    and annotation text, unwrapping ``Annotated[T, ...]`` wrappers to
    expose the inner type. Reads the return annotation if present, but
    this is often a generic ``dict`` literal. Literal return-dict keys
    are preferred when present; otherwise the generic return annotation
    is exposed as ``{"result": "<type>"}``.

    This function never raises. On any parse error, returns two empty
    dicts so callers can preserve the generated artifact's explicit
    schemas or safely register without inferred schemas.

    Examples::

        >>> src = '''
        ... from typing import Annotated
        ... from mcp.server.fastmcp import FastMCP
        ... mcp = FastMCP("x")
        ... @mcp.tool()
        ... async def parse_csv(
        ...     file_path: Annotated[str, "Path to the CSV"],
        ... ) -> dict:
        ...     ...
        ... '''
        >>> inputs, outputs = extract_mcp_tool_signature(src)
        >>> inputs
        {'file_path': 'str'}
        >>> outputs
        {'result': 'dict'}

    The output schema ``{"result": "dict"}`` is a generic placeholder
    that says "this function returns a dict"; artifact-provided schemas
    remain the preferred source when available.
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError as exc:
        logger.debug("extract_mcp_tool_signature: parse error: %s", exc)
        return {}, {}

    target: ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if _is_mcp_tool_decorated(node):
            target = node
            break

    if target is None:
        # Fall back to the first async def — handles generated code
        # that uses a non-standard decorator chain.
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                target = node
                break

    if target is None:
        return {}, {}

    input_schema: dict[str, str] = {}
    upstream_param_names: set[str] = set()

    # Regular positional + keyword arguments
    for arg in target.args.args:
        if arg.arg in ("self", "cls"):
            continue
        type_str = _annotation_to_type_string(arg.annotation)
        input_schema[arg.arg] = type_str
        # Chain middle/last steps have a generic `upstream_data: dict`
        # parameter. Track these so we can replace them below with the
        # specific keys actually subscripted inside the function body.
        if arg.arg in ("upstream_data", "inputs", "state") and type_str in (
            "dict",
            "Dict",
            "any",
        ):
            upstream_param_names.add(arg.arg)

    # Keyword-only arguments (after * or *args)
    for arg in target.args.kwonlyargs:
        type_str = _annotation_to_type_string(arg.annotation)
        input_schema[arg.arg] = type_str

    # If the function has an `upstream_data` (or similar) generic dict
    # parameter, scan the body for `upstream_data["key"]` / .get("key")
    # accesses and replace the generic parameter with the specific keys.
    # This recovers the effective input contract of chain middle/last
    # steps, which would otherwise all look identical under Jaccard.
    if upstream_param_names:
        accessed_keys = _scan_subscripted_keys(target, upstream_param_names)
        if accessed_keys:
            for param in upstream_param_names:
                input_schema.pop(param, None)
            for key in accessed_keys:
                # Mark these as "any" — type comes from upstream's output_schema
                input_schema.setdefault(key, "any")

    # Scan for `return {"key1": ..., "key2": ...}` dict literals.
    # This gives the actual output contract of the generated code,
    # which is strictly better than the generic return annotation.
    output_schema: dict[str, str] = _scan_return_dict_keys(target)
    if not output_schema:
        # Fall back to the generic return annotation.
        return_type = _annotation_to_type_string(target.returns)
        if return_type and return_type != "any":
            output_schema["result"] = return_type

    return input_schema, output_schema


def _scan_subscripted_keys(
    func: ast.AsyncFunctionDef, param_names: set[str]
) -> set[str]:
    """Collect literal string keys used to subscript any of *param_names*.

    Finds ``upstream_data["joined_sales"]``, ``upstream_data.get("inventory")``,
    and similar patterns. Returns the set of keys accessed. Keys that are
    dynamic (not literal strings) are ignored.
    """
    keys: set[str] = set()
    for node in ast.walk(func):
        # param["key"]
        if isinstance(node, ast.Subscript):
            if (
                isinstance(node.value, ast.Name)
                and node.value.id in param_names
            ):
                key = _literal_string(node.slice)
                if key is not None:
                    keys.add(key)
        # param.get("key") or param.get("key", default)
        elif isinstance(node, ast.Call):
            func_node = node.func
            if (
                isinstance(func_node, ast.Attribute)
                and func_node.attr == "get"
                and isinstance(func_node.value, ast.Name)
                and func_node.value.id in param_names
                and node.args
            ):
                key = _literal_string(node.args[0])
                if key is not None:
                    keys.add(key)
    return keys


def _scan_return_dict_keys(func: ast.AsyncFunctionDef) -> dict[str, str]:
    """Collect literal keys from any ``return {...}`` dict literal in *func*.

    Finds ``return {"row_count": len(rows), "column_names": [...]}`` and
    similar patterns. Keys with non-literal string values are skipped.
    Types are inferred from the value expression where tractable, or
    fall back to ``"any"``.

    If multiple return statements exist, their keys are merged (last
    write wins on type). This is an approximation for generated tools
    which typically have a single authoritative return site.
    """
    schema: dict[str, str] = {}
    for node in ast.walk(func):
        if not isinstance(node, ast.Return):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for key_node, value_node in zip(node.value.keys, node.value.values):
            key = _literal_string(key_node)
            if key is None:
                continue
            schema[key] = _infer_value_type(value_node)
    return schema


def _literal_string(node: ast.AST | None) -> str | None:
    """Return the literal string value of *node*, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _infer_value_type(node: ast.AST) -> str:
    """Best-effort type inference from an expression node.

    Recognises common Python patterns: literal constants, list/dict
    literals, and ``int()`` / ``float()`` / ``str()`` calls. Falls
    back to ``"any"`` for anything else.
    """
    if isinstance(node, ast.Constant):
        return type(node.value).__name__
    if isinstance(node, ast.List):
        return "list"
    if isinstance(node, ast.Dict):
        return "dict"
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id in ("int", "float", "str", "bool", "list", "dict"):
            return fn.id
    return "any"


def _is_mcp_tool_decorated(node: ast.AsyncFunctionDef) -> bool:
    """Return True if *node* has an @mcp.tool decorator (called or bare)."""
    for dec in node.decorator_list:
        # @mcp.tool()  — Call whose func is an Attribute
        if isinstance(dec, ast.Call):
            if _is_mcp_tool_attr(dec.func):
                return True
        # @mcp.tool  — bare Attribute
        elif _is_mcp_tool_attr(dec):
            return True
    return False


def _is_mcp_tool_attr(node: ast.AST | None) -> bool:
    """Return True if *node* is the Attribute expression ``mcp.tool``."""
    if not isinstance(node, ast.Attribute):
        return False
    if node.attr != "tool":
        return False
    # node.value should be a Name "mcp" (or something that looks like it)
    return isinstance(node.value, ast.Name) and node.value.id == "mcp"


def _annotation_to_type_string(annotation: ast.AST | None) -> str:
    """Convert an AST annotation node to a short type string.

    Unwraps ``Annotated[T, ...]`` to return T alone. For subscripted
    generics like ``list[dict]`` or ``dict[str, int]``, returns the
    source text. Falls back to ``"any"`` on unrecognised shapes.
    """
    if annotation is None:
        return "any"

    # Unwrap Annotated[T, metadata...] → T
    if isinstance(annotation, ast.Subscript):
        base = annotation.value
        if isinstance(base, ast.Name) and base.id == "Annotated":
            inner = annotation.slice
            if isinstance(inner, ast.Tuple) and inner.elts:
                return _annotation_to_type_string(inner.elts[0])
            return _annotation_to_type_string(inner)

    try:
        return ast.unparse(annotation).strip()
    except Exception:  # noqa: BLE001
        return "any"


def find_subtask_for_gap(
    gap_query: str | None,
    subtasks: list[SubTask],
) -> tuple[SubTask | None, int | None]:
    """Find the subtask whose required_capabilities contain *gap_query*.

    Returns (subtask, index) or (None, None) if no match. Mirrors the
    matching rule already used by ``build_schema_context`` in
    ``generators/utils.py``.
    """
    if not gap_query:
        return None, None
    for idx, st in enumerate(subtasks):
        if gap_query in st.required_capabilities:
            return st, idx
    return None, None


def derive_input_schema_from_upstream(
    subtask: SubTask,
    all_subtasks: list[SubTask],
) -> dict[str, str]:
    """Merge upstream subtasks' output_schemas into this step's input contract.

    For a middle or last step in a compositional chain, the input contract
    is the union of its dependencies' output_schemas. For a first step
    with no dependencies, returns an empty dict.
    """
    if not subtask.depends_on:
        return {}
    merged: dict[str, str] = {}
    for dep_idx in subtask.depends_on:
        if 0 <= dep_idx < len(all_subtasks):
            dep = all_subtasks[dep_idx]
            if dep.output_schema:
                for key, type_desc in dep.output_schema.items():
                    merged[key] = type_desc
    return merged
