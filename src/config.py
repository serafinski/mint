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

#: File extensions treated as binary (not read as text for prompt augmentation).
BINARY_EXTENSIONS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".ico",
    ".webp", ".svg", ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav",
    ".flac", ".ogg", ".webm", ".pdf", ".zip", ".gz", ".tar", ".bz2",
    ".7z", ".rar", ".xz", ".exe", ".dll", ".so", ".dylib", ".bin",
    ".dat", ".db", ".sqlite", ".sqlite3", ".duckdb", ".parquet",
    ".pickle", ".pkl", ".npy", ".npz", ".h5", ".hdf5",
})

# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------

#: Number of invocations before auto-enabling the optimizer in CLI mode.
AUTO_OPTIMIZER_THRESHOLD: int = 20
