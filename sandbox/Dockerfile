FROM python:3.11-slim

# Install uv for fast package installation at runtime (~10-50x faster than pip).
# Generated tools declare their dependencies via pip_packages; these are
# installed dynamically in ephemeral containers using uv pip install.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Minimal base image — only mcp and pydantic pre-installed because generated
# MCP tools always import them (FastMCP pattern). All other dependencies
# (pandas, numpy, sklearn, etc.) are resolved dynamically via LLM-based
# import→PyPI mapping and installed at runtime in ephemeral containers.
RUN uv pip install --system --no-cache \
    "mcp>=1.26.0" \
    "pydantic>=2.12.5"

RUN useradd --create-home --shell /bin/bash sandbox

WORKDIR /home/sandbox
USER sandbox

# Default: run a Python script passed as a mounted file
ENTRYPOINT ["python"]
CMD ["--help"]
