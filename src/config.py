"""Centralized configuration constants for the JIT Capability Synthesis system.

Single source of truth for model names, thresholds, timeouts, and other
values that were previously scattered across multiple files.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# LLM model
# ---------------------------------------------------------------------------

#: Unified LLM model identifier used by all components (generators, classifier,
#: decomposition, adversarial probing, optimizer, etc.).
#: Override via the ``JIT_LLM_MODEL`` environment variable for experiments.
DEFAULT_LLM_MODEL: str = os.getenv("JIT_LLM_MODEL", "openai:gpt-5-mini")

# ---------------------------------------------------------------------------
# Sandbox / Docker
# ---------------------------------------------------------------------------

#: Docker image name for sandboxed code execution.
#: Override via ``JIT_SANDBOX_IMAGE`` for custom sandbox images.
SANDBOX_IMAGE: str = os.getenv("JIT_SANDBOX_IMAGE", "jit-sandbox")

#: Packages pre-installed in the sandbox Dockerfile (mcp + pydantic).
#: Must be kept in sync with ``sandbox/Dockerfile``.
DOCKER_PREINSTALLED: frozenset[str] = frozenset({"mcp", "pydantic"})

# ---------------------------------------------------------------------------
# File handling
# ---------------------------------------------------------------------------

#: Maximum file size (bytes) for user-provided input files.
FILE_SIZE_LIMIT_BYTES: int = 10 * 1024 * 1024  # 10 MB

#: File extensions treated as binary.  Used for two purposes:
#:
#: 1. **Input file validation**: files matching these
#:    extensions are rejected *unless* they are also in the "processable
#:    binary" carve-out (currently ``.xlsx`` / ``.xls``, handled via openpyxl).
#: 2. **Sandbox file transfer** (``_execute_mcp_tool``): matching files are
#:    base64-encoded before injection into the Docker sandbox, since they
#:    cannot survive a plain-text write.
#:
#: The business-analytics testbed routinely produces and consumes Excel
#: (``.xlsx``/``.xls``), Parquet, and Pickle files — keep those here so
#: the sandbox transfer path handles them correctly.
BINARY_EXTENSIONS: frozenset[str] = frozenset({
    # Images
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".ico",
    ".webp", ".svg",
    # Audio / video
    ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav",
    ".flac", ".ogg", ".webm",
    # Documents
    ".pdf",
    # Archives
    ".zip", ".gz", ".tar", ".bz2", ".7z", ".rar", ".xz",
    # Executables / shared libs
    ".exe", ".dll", ".so", ".dylib",
    # Data — common in the business-analytics testbed
    ".xlsx", ".xls",
    ".parquet",
    ".pickle", ".pkl",
    ".bin", ".dat",
    # Databases
    ".db", ".sqlite", ".sqlite3", ".duckdb",
    # Scientific / ML
    ".npy", ".npz", ".h5", ".hdf5",
})

# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------

#: Number of invocations before auto-enabling the optimizer.
AUTO_OPTIMIZER_THRESHOLD: int = 20

# ---------------------------------------------------------------------------
# Verification (Pillar 2)
# ---------------------------------------------------------------------------

#: Reasoning effort passed to the adversarial DeepAgent's LLM (Pillar 2 Tier 3).
#:
#: Default ``"low"`` keeps Tier 3 cost bounded (~$0.01-0.02 per artifact at
#: gpt-5-mini); raising to ``"medium"`` or ``"high"`` improves the prober's
#: ability to detect sophisticated deceptive patterns at proportional cost.
#: Override via ``JIT_ADVERSARIAL_REASONING`` for ablation studies — e.g.,
#: run a 20-artifact subset at ``"high"`` and report the M2 detection delta
#: in Chapter 4.5 ("reasoning-effort ablation: X% additional contamination
#: caught at high vs. low"). Security audit finding #99 (2026-04-19, ext10)
#: surfaced the pre-fix hardcoded-low setting at adversarial.py:354 as a
#: capped Tier 3 quality knob with no override path.
ADVERSARIAL_REASONING_EFFORT: str = os.getenv(
    "JIT_ADVERSARIAL_REASONING", "low"
)
