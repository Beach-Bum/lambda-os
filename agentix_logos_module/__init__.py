"""agentix-logos-module — Logos module exposing Agentix control surface.

This package contains the reference Python implementation of the
Logos module. The Logos runtime ultimately loads a C++/Qt plugin shim
that wraps these same method bodies; in Phase 2 (this issue) the
contract is proven via the Python reference + ``subprocess.run``
mocks.

Entry point: :class:`agentix_logos_module.AgentixLogosModule`.
"""

from agentix_logos_module.agentix_logos_module import (
    AgentixLogosModule,
    ModuleError,
    ModuleResult,
)

__all__ = ["AgentixLogosModule", "ModuleError", "ModuleResult"]
__version__ = "0.1.0"
