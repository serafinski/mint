"""Centralized configuration constants for the JIT Capability Synthesis system.

Single source of truth for model names, sandbox settings, optimizer
thresholds, and verification tuning.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# LLM model
# ---------------------------------------------------------------------------

#: Unified LLM model identifier used by all components (generators, classifier,
#: decomposition, adversarial probing, optimizer, etc.).
#: Override via the ``LLM_MODEL`` environment variable for experiments.
DEFAULT_LLM_MODEL: str = os.getenv("LLM_MODEL", "openai:gpt-5-mini")

# ---------------------------------------------------------------------------
# Sandbox / Docker
# ---------------------------------------------------------------------------

#: Docker image name for sandboxed code execution.
#: Override via ``SANDBOX_IMAGE`` for custom sandbox images.
SANDBOX_IMAGE: str = os.getenv("SANDBOX_IMAGE", "jit-sandbox")

#: Packages pre-installed in the sandbox Dockerfile (mcp + pydantic).
#: Must be kept in sync with ``sandbox/Dockerfile``.
DOCKER_PREINSTALLED: frozenset[str] = frozenset({"mcp", "pydantic"})

#: Number of invocations before auto-enabling optimizer operations.
AUTO_OPTIMIZER_THRESHOLD: int = 20

# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

#: Reasoning effort passed to the adversarial DeepAgent's LLM.
#:
#: Default ``"low"`` keeps Tier 3 cost bounded (~$0.01-0.02 per artifact at
#: gpt-5-mini); raising to ``"medium"`` or ``"high"`` improves the prober's
#: ability to detect sophisticated deceptive patterns at proportional cost.
#: Override via ``JIT_ADVERSARIAL_REASONING`` for targeted ablation studies
#: without changing source code.
ADVERSARIAL_REASONING_EFFORT: str = os.getenv(
    "JIT_ADVERSARIAL_REASONING", "low"
)
