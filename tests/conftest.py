"""Shared pytest configuration and fixtures."""

from dotenv import load_dotenv

# Load .env at test collection time so all tests see the environment variables.
load_dotenv()
