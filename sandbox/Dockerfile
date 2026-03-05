FROM python:3.11-slim

# Install the packages that generated MCP tools are allowed to import.
# Generator prompts restrict imports to stdlib + mcp + pydantic only.
# Note: pip install runs at BUILD time; --network none applies at RUN time only.
RUN pip install --no-cache-dir "mcp>=1.26.0" "pydantic>=2.12.5"

RUN useradd --create-home --shell /bin/bash sandbox

WORKDIR /home/sandbox
USER sandbox

# Default: run a Python script passed as a mounted file
ENTRYPOINT ["python"]
CMD ["--help"]
