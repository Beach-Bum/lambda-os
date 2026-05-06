"""Logoscore method dispatch shim.

When a Logos module is invoked via:

    logoscore -m <build-dir> -l agentix_logos_module \\
        -c "agentix_logos_module.controller_plan(/path/to/ws)"

the runtime needs an entry point that maps the string-form method call
to a Python (or C++) function call with parsed arguments. This module
provides the Python-side dispatch used by the reference shim and the
test suite.

The eventual C++/Qt plugin replicates the same dispatch — same method
names, same argument shapes, same return JSON.
"""

from __future__ import annotations

import json
from typing import Any

from agentix_logos_module.agentix_logos_module import (
    AgentixLogosModule,
    ModuleResult,
)


def dispatch(method_call: str, *, module: AgentixLogosModule | None = None) -> str:
    """Parse and dispatch a logoscore-style ``"module.method(args)"`` call.

    Args:
        method_call: A string of the form
            ``"agentix_logos_module.<method>(<args>)"``. ``args`` is
            comma-separated; strings need not be quoted (logoscore's
            convention).
        module: Optional pre-built :class:`AgentixLogosModule` instance.
            If None, a fresh default-configured one is constructed.

    Returns:
        JSON string of the :class:`ModuleResult`. Always JSON — failures
        are encoded as ``ok=False`` rather than exceptions, so the
        logoscore caller always gets structured output.

    Raises:
        ValueError: If ``method_call`` is malformed (no method
            recognised, args don't match the method signature).
    """
    parsed_method, args = _parse_call(method_call)
    mod = module or AgentixLogosModule()

    handler = METHODS.get(parsed_method)
    if handler is None:
        raise ValueError(
            f"unknown method {parsed_method!r}; supported: {sorted(METHODS)}"
        )
    result: ModuleResult = handler(mod, args)
    return json.dumps(result.to_dict(), sort_keys=True)


def _parse_call(method_call: str) -> tuple[str, list[str]]:
    """Parse ``"agentix_logos_module.method(arg1, arg2)"`` into (method, args).

    Args:
        method_call: The method-call string to parse.

    Returns:
        Tuple of (method_name, args_list). Args are stripped strings;
        the dispatch layer coerces types per method.

    Raises:
        ValueError: If the syntax doesn't match the expected pattern.
    """
    s = method_call.strip()
    # Strip module prefix if present.
    prefix = "agentix_logos_module."
    if s.startswith(prefix):
        s = s[len(prefix) :]
    if "(" not in s or not s.endswith(")"):
        raise ValueError(
            f"malformed method call {method_call!r}; expected 'name(args)'"
        )
    open_idx = s.index("(")
    method = s[:open_idx]
    inside = s[open_idx + 1 : -1].strip()
    if not inside:
        return method, []
    args = [a.strip() for a in inside.split(",")]
    return method, args


def _h_controller_plan(mod: AgentixLogosModule, args: list[str]) -> ModuleResult:
    if len(args) != 1:
        raise ValueError(
            f"controller_plan(workspace_path) takes 1 argument; got {len(args)}"
        )
    return mod.controller_plan(args[0])


def _h_controller_run(mod: AgentixLogosModule, args: list[str]) -> ModuleResult:
    if len(args) != 2:
        raise ValueError(
            f"controller_run(goal, workspace_path) takes 2 arguments; got {len(args)}"
        )
    return mod.controller_run(args[0], args[1])


def _h_audit_tail(mod: AgentixLogosModule, args: list[str]) -> ModuleResult:
    if len(args) not in (1, 2):
        raise ValueError(
            f"audit_tail(workspace_path[, lines]) takes 1-2 arguments; got {len(args)}"
        )
    workspace = args[0]
    lines = int(args[1]) if len(args) == 2 else 10
    return mod.audit_tail(workspace, lines)


def _h_policy_check(mod: AgentixLogosModule, args: list[str]) -> ModuleResult:
    if len(args) != 1:
        raise ValueError(
            f"policy_check(workspace_path) takes 1 argument; got {len(args)}"
        )
    return mod.policy_check(args[0])


METHODS: dict[str, Any] = {
    "controller_plan": _h_controller_plan,
    "controller_run": _h_controller_run,
    "audit_tail": _h_audit_tail,
    "policy_check": _h_policy_check,
}
