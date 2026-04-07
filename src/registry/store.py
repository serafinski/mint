from __future__ import annotations

import json
from datetime import UTC, datetime

import chromadb

from src.config import DEFAULT_LLM_MODEL
from src.schemas.capability import CapabilityDescriptor, CapabilityType


class SkillRegistry:
    """ChromaDB-backed capability registry with hierarchical semantic search."""

    def __init__(
        self,
        *,
        persist_directory: str | None = None,
        collection_name: str = "capabilities",
    ) -> None:
        if persist_directory:
            self._client = chromadb.PersistentClient(path=persist_directory)
        else:
            self._client = chromadb.EphemeralClient()
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    # --- Private helpers ---

    @staticmethod
    def _build_document(capability: CapabilityDescriptor) -> str:
        """Build hierarchical embedding document for richer semantic search.

        Format: "{domain} > {capability_type} > {name}: {description}"
        """
        domain = getattr(capability, "domain", "general")
        return (
            f"{domain} > {capability.capability_type.value} > "
            f"{capability.name}: {capability.description}"
        )

    @staticmethod
    def _build_metadata(capability: CapabilityDescriptor) -> dict:
        """Extract all CapabilityDescriptor fields into ChromaDB-compatible metadata.

        ChromaDB metadata values must be str, int, float, or bool.
        """
        meta: dict = {
            "capability_type": capability.capability_type.value,
            "version": capability.version,
            "created_at": capability.created_at.isoformat(),
            "raw_description": capability.description,
            # New fields — use getattr for schema compatibility
            "domain": getattr(capability, "domain", "general"),
            "is_active": getattr(capability, "is_active", True),
            "tags": ",".join(getattr(capability, "tags", [])),
            "input_schema": json.dumps(getattr(capability, "input_schema", {})),
            "output_schema": json.dumps(getattr(capability, "output_schema", {})),
            "extra_metadata": json.dumps(
                getattr(capability, "metadata", {}), default=str
            ),
        }
        return meta

    @staticmethod
    def _descriptor_from_metadata(
        name: str, meta: dict
    ) -> CapabilityDescriptor:
        """Reconstruct a CapabilityDescriptor from ChromaDB metadata."""
        model_fields = CapabilityDescriptor.model_fields

        kwargs: dict = {
            "name": name,
            "description": meta.get("raw_description", ""),
            "capability_type": CapabilityType(meta["capability_type"]),
            "version": meta["version"],
        }

        # Restore created_at from stored ISO string; fall back to now() if missing/corrupt.
        if "created_at" in meta:
            try:
                kwargs["created_at"] = datetime.fromisoformat(meta["created_at"])
            except (ValueError, TypeError):
                kwargs["created_at"] = datetime.now(UTC)

        # Conditionally add fields the schema supports
        if "domain" in model_fields and "domain" in meta:
            kwargs["domain"] = meta["domain"]
        if "is_active" in model_fields and "is_active" in meta:
            kwargs["is_active"] = meta["is_active"]
        if "tags" in model_fields and "tags" in meta:
            raw_tags = meta["tags"]
            kwargs["tags"] = [t for t in raw_tags.split(",") if t] if raw_tags else []
        if "input_schema" in model_fields and "input_schema" in meta:
            kwargs["input_schema"] = json.loads(meta["input_schema"])
        if "output_schema" in model_fields and "output_schema" in meta:
            kwargs["output_schema"] = json.loads(meta["output_schema"])
        if "metadata" in model_fields and "extra_metadata" in meta:
            kwargs["metadata"] = json.loads(meta["extra_metadata"])

        return CapabilityDescriptor(**kwargs)

    # --- CRUD operations ---

    def add(self, capability: CapabilityDescriptor) -> None:
        """Register a capability. Overwrites if the name already exists."""
        self._collection.upsert(
            ids=[capability.name],
            documents=[self._build_document(capability)],
            metadatas=[self._build_metadata(capability)],
        )

    def get(self, name: str) -> CapabilityDescriptor | None:
        """Exact lookup by name. Returns None if not found."""
        result = self._collection.get(ids=[name])
        if not result["ids"]:
            return None
        meta = result["metadatas"][0]
        return self._descriptor_from_metadata(name, meta)

    def update(self, name: str, **fields: object) -> CapabilityDescriptor:
        """Partial update of a capability. Returns the updated descriptor.

        Raises KeyError if the capability does not exist.
        """
        existing = self.get(name)
        if existing is None:
            raise KeyError(f"Capability '{name}' not found in registry")
        updated = existing.model_copy(update=fields)
        self.add(updated)
        return updated

    def deactivate(self, name: str) -> None:
        """Soft-delete: set is_active=False in metadata.

        Works regardless of whether the schema has the is_active field yet.
        Raises KeyError if the capability does not exist.
        """
        existing = self.get(name)
        if existing is None:
            raise KeyError(f"Capability '{name}' not found in registry")

        # If schema supports is_active, update via model; otherwise just update metadata
        if "is_active" in CapabilityDescriptor.model_fields:
            self.update(name, is_active=False)
        else:
            # Directly update ChromaDB metadata
            self._collection.update(
                ids=[name],
                metadatas=[{**self._build_metadata(existing), "is_active": False}],
            )

    def delete(self, name: str) -> None:
        """Hard delete a capability from the registry.

        Raises KeyError if the capability does not exist.
        """
        existing = self.get(name)
        if existing is None:
            raise KeyError(f"Capability '{name}' not found in registry")
        self._collection.delete(ids=[name])

    def list_all(
        self,
        *,
        capability_type: CapabilityType | None = None,
        domain: str | None = None,
        is_active: bool | None = True,
    ) -> list[CapabilityDescriptor]:
        """List capabilities with optional filters.

        Args:
            capability_type: Filter by type (MCP_TOOL, SKILL, AGENT).
            domain: Filter by domain string.
            is_active: Filter by active status (default True = active only). Pass None to include all.
        """
        where_conditions: list[dict] = []
        if capability_type is not None:
            where_conditions.append(
                {"capability_type": {"$eq": capability_type.value}}
            )
        if domain is not None:
            where_conditions.append({"domain": {"$eq": domain}})
        if is_active is not None:
            where_conditions.append({"is_active": {"$eq": is_active}})

        kwargs: dict = {}
        if len(where_conditions) == 1:
            kwargs["where"] = where_conditions[0]
        elif len(where_conditions) > 1:
            kwargs["where"] = {"$and": where_conditions}

        result = self._collection.get(**kwargs)

        descriptors: list[CapabilityDescriptor] = []
        for name, meta in zip(result["ids"], result["metadatas"]):
            descriptors.append(self._descriptor_from_metadata(name, meta))
        return descriptors

    # --- Search ---

    def search(
        self,
        query: str,
        n_results: int = 3,
        *,
        min_confidence: float = 0.0,
        is_active: bool | None = True,
    ) -> list[tuple[CapabilityDescriptor, float]]:
        """Semantic search. Returns (capability, score) pairs sorted by relevance.

        Score is cosine similarity (0-1, higher is better).

        Args:
            query: Natural-language search query.
            n_results: Maximum number of results to return.
            min_confidence: Minimum similarity score to include in results.
            is_active: Filter by active status. ``True`` (default) returns only
                active capabilities; ``False`` returns only inactive
                capabilities; ``None`` returns all regardless of status.
        """
        results = self._collection.query(query_texts=[query], n_results=n_results)

        pairs: list[tuple[CapabilityDescriptor, float]] = []
        if not results["ids"] or not results["ids"][0]:
            return pairs

        for name, meta, distance in zip(
            results["ids"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            score = 1.0 - distance  # cosine distance -> similarity
            if score < min_confidence:
                continue
            descriptor = self._descriptor_from_metadata(name, meta)
            if is_active is not None and descriptor.is_active != is_active:
                continue
            pairs.append((descriptor, score))
        return pairs

    def detect_gaps(
        self, queries: list[str], *, threshold: float = 0.5
    ) -> tuple[list[tuple[CapabilityDescriptor, float, str]], list[tuple[str, str]]]:
        """For each query, search the registry and classify as match or gap.

        Returns:
            matches: list of (capability, score, original_query) above threshold
            gaps: list of (query, reason) below threshold
        """
        matches: list[tuple[CapabilityDescriptor, float, str]] = []
        gaps: list[tuple[str, str]] = []

        for query in queries:
            # search() filters out is_active=False, so also query unfiltered
            # to detect deactivated capabilities and produce accurate gap reasons.
            results = self.search(query, n_results=1)
            unfiltered = self.search(query, n_results=1, is_active=None)

            if results and results[0][1] >= threshold:
                cap, score = results[0]
                matches.append((cap, score, query))
            else:
                # Check whether a deactivated capability would have matched.
                if (
                    unfiltered
                    and unfiltered[0][1] >= threshold
                    and not unfiltered[0][0].is_active
                ):
                    reason = f"capability '{unfiltered[0][0].name}' is deactivated"
                else:
                    best_score = results[0][1] if results else 0.0
                    reason = f"best score {best_score:.2f} below threshold {threshold:.2f}"
                gaps.append((query, reason))

        return matches, gaps

    @property
    def count(self) -> int:
        return self._collection.count()


def seed_registry(registry: SkillRegistry) -> None:
    """Seed with diverse capabilities spanning all three types."""
    # Build kwargs for optional fields — only include if schema supports them
    model_fields = CapabilityDescriptor.model_fields
    has_domain = "domain" in model_fields
    has_tags = "tags" in model_fields

    def _cap(
        name: str,
        description: str,
        capability_type: CapabilityType,
        domain: str = "general",
        tags: list[str] | None = None,
        source_code: str | None = None,
        skill_content: str | None = None,
        agent_config: str | None = None,
    ) -> CapabilityDescriptor:
        metadata: dict = {"pinned": "true"}  # all seeded caps are pinned
        if source_code is not None:
            metadata["source_code"] = source_code
        if skill_content is not None:
            metadata["skill_content"] = skill_content
        if agent_config is not None:
            metadata["agent_config"] = agent_config
        kwargs: dict = {
            "name": name,
            "description": description,
            "capability_type": capability_type,
            "metadata": metadata,
        }
        if has_domain:
            kwargs["domain"] = domain
        if has_tags and tags:
            kwargs["tags"] = tags
        return CapabilityDescriptor(**kwargs)

    seeds = [
        # MCP_TOOL type
        _cap(
            "fibonacci",
            "Compute the nth Fibonacci number",
            CapabilityType.MCP_TOOL,
            domain="mathematics",
            tags=["math", "computation", "sequence"],
            source_code=(
                "from typing import Annotated\n"
                "from mcp.server.fastmcp import FastMCP\n"
                "from pydantic import Field\n"
                'mcp = FastMCP("fibonacci_mcp")\n'
                "@mcp.tool()\n"
                "async def fibonacci(\n"
                "    n: Annotated[int, Field(description="
                '"Index into the Fibonacci sequence", ge=0, le=1000)],\n'
                ") -> int:\n"
                '    """Compute the nth Fibonacci number (0-indexed)."""\n'
                "    a, b = 0, 1\n"
                "    for _ in range(n):\n"
                "        a, b = b, a + b\n"
                "    return a\n"
                'if __name__ == "__main__":\n'
                "    mcp.run()\n"
            ),
        ),
        _cap(
            "csv-parser",
            "Parse a CSV file and return structured rows with headers and typed values",
            CapabilityType.MCP_TOOL,
            domain="data-processing",
            tags=["csv", "parsing", "data-ingestion"],
            source_code=(
                'from typing import Annotated\n'
                'from mcp.server.fastmcp import FastMCP\n'
                'mcp = FastMCP("csv-parser")\n'
                '@mcp.tool()\n'
                'async def csv_parser(file_path: Annotated[str, "Path to the CSV file"]) -> dict:\n'
                '    """Parse a CSV file and return row_count and column_names."""\n'
                '    import csv\n'
                '    with open(file_path) as f:\n'
                '        reader = csv.DictReader(f)\n'
                '        rows = list(reader)\n'
                '        return {"row_count": len(rows), "column_names": list(reader.fieldnames or [])}\n'
                'if __name__ == "__main__":\n'
                '    mcp.run()\n'
            ),
        ),
        _cap(
            "date-formatter",
            (
                "Format a date string between different date formats"
                " such as ISO-8601, RFC-2822, and custom patterns"
            ),
            CapabilityType.MCP_TOOL,
            domain="data-processing",
            tags=["date", "formatting", "conversion"],
        ),
        _cap(
            "json-validator",
            "Validate JSON data against a JSON Schema and return detailed validation errors",
            CapabilityType.MCP_TOOL,
            domain="data-processing",
            tags=["json", "validation", "schema"],
            source_code=(
                "from typing import Annotated\n"
                "from mcp.server.fastmcp import FastMCP\n"
                'mcp = FastMCP("json-validator")\n'
                "@mcp.tool()\n"
                "async def json_validator(\n"
                '    config_path: Annotated[str, "Path to JSON config"],\n'
                '    schema_path: Annotated[str, "Path to JSON Schema"],\n'
                ") -> dict:\n"
                '    """Validate JSON data against a JSON Schema and return errors."""\n'
                "    import json, jsonschema\n"
                "    config = json.loads(open(config_path).read())\n"
                "    schema = json.loads(open(schema_path).read())\n"
                "    validator = jsonschema.Draft7Validator(schema)\n"
                "    errors = [e.message for e in validator.iter_errors(config)]\n"
                '    return {"errors": errors}\n'
                'if __name__ == "__main__":\n'
                "    mcp.run()\n"
            ),
        ),
        _cap(
            "text-statistics",
            (
                "Compute word count, sentence count, readability score,"
                " and other text metrics for a given document"
            ),
            CapabilityType.MCP_TOOL,
            domain="text-analysis",
            tags=["text", "statistics", "nlp", "readability"],
            source_code=(
                'from typing import Annotated\n'
                'from mcp.server.fastmcp import FastMCP\n'
                'import re\n'
                'mcp = FastMCP("text-statistics")\n'
                '@mcp.tool()\n'
                'async def text_statistics(file_path: Annotated[str, "Path to text file"]) -> dict:\n'
                '    """Compute word count and Flesch readability score."""\n'
                '    text = open(file_path).read()\n'
                '    words = re.findall(r"\\b\\w+\\b", text)\n'
                '    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]\n'
                '    def syllables(w):\n'
                '        return max(len(re.findall(r"[aeiouAEIOU]+", w)), 1)\n'
                '    n_w = len(words)\n'
                '    n_s = max(len(sentences), 1)\n'
                '    n_syl = sum(syllables(w) for w in words)\n'
                '    score = round(max(0.0, min(100.0, 206.835 - 1.015*(n_w/n_s) - 84.6*(n_syl/max(n_w,1)))), 2)\n'
                '    return {"word_count": n_w, "readability_score": score}\n'
                'if __name__ == "__main__":\n'
                '    mcp.run()\n'
            ),
        ),
        # SKILL type
        _cap(
            "data-cleaning-workflow",
            (
                "Multi-step workflow for cleaning messy datasets"
                " including null handling, deduplication,"
                " type coercion, and outlier flagging"
            ),
            CapabilityType.SKILL,
            domain="data-processing",
            tags=["data-cleaning", "etl", "workflow", "preprocessing"],
            skill_content=(
                "---\n"
                "name: data-cleaning-workflow\n"
                "description: Multi-step data cleaning workflow\n"
                "---\n\n"
                "# Data Cleaning Workflow\n\n"
                "## Steps\n\n"
                "1. **Load data** — Read the input CSV/JSON file\n"
                "2. **Remove nulls** — Drop rows where all values "
                "are null; flag rows with partial nulls\n"
                "3. **Deduplicate** — Remove exact duplicate rows, "
                "keeping the first occurrence\n"
                "4. **Type coercion** — Convert numeric strings to "
                "numbers, parse date columns\n"
                "5. **Outlier flagging** — Flag values beyond 3 "
                "standard deviations from the mean\n\n"
                "## Output\n\n"
                "Return a dict with: `clean_row_count`, "
                "`removed_nulls`, `removed_duplicates`, "
                "`outliers_flagged`, and `type_errors`.\n"
            ),
        ),
        _cap(
            "report-generation",
            (
                "Generate formatted analysis reports from structured"
                " data with tables, charts descriptions,"
                " and executive summaries"
            ),
            CapabilityType.SKILL,
            domain="reporting",
            tags=["report", "visualization", "summary", "documentation"],
            skill_content=(
                "---\n"
                "name: report-generation\n"
                "description: Generate analysis reports from data\n"
                "---\n\n"
                "# Report Generation\n\n"
                "## Steps\n\n"
                "1. **Compute summary statistics** — mean, median, "
                "std, min, max for numeric columns\n"
                "2. **Build stats table** — Format as a dict of "
                "column → statistics\n"
                "3. **Identify anomalies** — Find the top 5 outlier "
                "records by z-score on the primary metric\n"
                "4. **Generate summary** — One-paragraph executive "
                "summary describing key findings\n\n"
                "## Output\n\n"
                "Return a dict with: `summary` (str), "
                "`stats_table` (dict), and `anomalies` (list of "
                "top 5 records).\n"
            ),
        ),
        # AGENT type
        _cap(
            "data-analysis-agent",
            (
                "Autonomous agent for exploratory data analysis"
                " including profiling, correlation analysis,"
                " pattern detection, and insight generation"
            ),
            CapabilityType.AGENT,
            domain="analytics",
            tags=["analysis", "eda", "profiling", "insights", "autonomous"],
            agent_config=(
                '{"system_prompt": "You are a data analysis agent. '
                "Given a dataset, perform exploratory data analysis:\\n"
                "1. Profile the data (row count, column types, "
                "missing values)\\n"
                "2. Compute summary statistics for numeric columns\\n"
                "3. Identify correlations between variables\\n"
                "4. Detect patterns and anomalies\\n"
                "5. Generate actionable insights\\n\\n"
                "Use the sandbox_execute tool to run Python code "
                "for analysis. Write findings to files for context "
                'management.", '
                '"node_config": {"name": "data_analysis_node", '
                f'"model": "{DEFAULT_LLM_MODEL}", '
                '"tools": ["sandbox_execute"], '
                '"description": "Autonomous EDA agent"}, '
                '"is_deep_agent": true}'
            ),
        ),
    ]
    for cap in seeds:
        registry.add(cap)
