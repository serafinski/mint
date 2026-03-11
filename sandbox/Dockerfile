FROM python:3.11-slim

# Install packages that generated MCP tools are allowed to import.
# Allowed imports: stdlib, mcp, pydantic, pandas, numpy
RUN pip install --no-cache-dir \
    "mcp>=1.26.0" \
    "pydantic>=2.12.5" \
    "pandas>=2.0.0" \
    "numpy>=1.24.0"

RUN useradd --create-home --shell /bin/bash sandbox

WORKDIR /home/sandbox
USER sandbox

# Default: run a Python script passed as a mounted file
ENTRYPOINT ["python"]
CMD ["--help"]
