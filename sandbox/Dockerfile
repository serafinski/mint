FROM python:3.11-slim

# No network access at runtime — install nothing extra
# This image runs generated Python code in isolation

RUN useradd --create-home --shell /bin/bash sandbox

WORKDIR /home/sandbox
USER sandbox

# Default: run a Python script passed as a mounted file
ENTRYPOINT ["python"]
CMD ["--help"]
