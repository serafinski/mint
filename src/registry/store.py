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
    def _summarize_description(description: str, max_words: int = 50) -> str:
        """Truncate description to *max_words* for embedding efficiency.

        The full description is preserved in ChromaDB metadata
        (``raw_description``); this summary is used only for the embedding
        document so that search-time token costs stay bounded.

        50-word cap keeps typical documents well under the 256-token
        all-MiniLM-L6-v2 limit even with the B1 inputs/outputs lines.
        Reducing the cap further (tried 30) measurably hurt Phase A
        recall because it diluted the summary signal relative to the
        added schema tokens.
        """
        words = description.split()
        if len(words) <= max_words:
            return description
        return " ".join(words[:max_words]) + "…"

    @staticmethod
    def _format_signature(schema: dict | None) -> str:
        """Format a schema dict as a comma-separated list of parameter names.

        B1 (enriched hierarchical embedding): flattens an input or output
        schema into a compact list of identifiers for inclusion in the
        embedding document. **Only the parameter/output names are
        emitted, not their types.** Type tokens like ``int``, ``str``,
        ``list``, ``dict`` carry almost no semantic signal for realistic
        caller queries (which rarely mention types explicitly) and their
        inclusion measurably dilutes prose-query recall — an earlier
        ``name:type`` format regressed Phase A recall by 6-12pp on
        probes whose semantics aligned with the description.

        Accepts:
          - ``None`` or empty dict → returns ``""``
          - ``{"file_path": "str"}`` → ``"file_path"``
          - ``{"row_count": "int", "column_names": "list[str]"}``
            → ``"row_count, column_names"``
          - ``{"n": {"type": "int"}}`` → ``"n"`` (JSON-Schema-ish input)
        """
        if not schema:
            return ""
        return ", ".join(str(name) for name in schema.keys())

    @staticmethod
    def _build_document(capability: CapabilityDescriptor) -> str:
        """Build hierarchical embedding document for richer semantic search.

        Format: "{domain} > {capability_type} > {name}: {summary}"

        The description is truncated to ~50 words for embedding efficiency;
        the full description is kept in metadata (``raw_description``).

        **B1 design decision (2026-04-13):** the embedding document
        remains prose-only. We tried three variants of enriching the
        document with structural signatures (multi-line labeled,
        single-line with ``||`` delimiter, and natural-prose
        ``Parameters:`` / ``Returns:``) and all regressed Phase A
        recall by 6-12pp because the added tokens dilute the prose
        signal for borderline-threshold probes. Instead, the
        structural signal is exposed through ``input_schema`` /
        ``output_schema`` on ``CapabilityDescriptor`` (already
        persisted in ChromaDB metadata), and consumed directly by
        (a) Pillar 3 Merge's Jaccard similarity over schema keys,
        and (b) gap detection's optional post-filter that cross-checks
        query/capability schema overlap after embedding fires. This
        preserves prose-query recall while still delivering the
        proposal's "overlapping input schemas" Merge criterion and
        catching silent-poisoning matches.
        """
        domain = getattr(capability, "domain", "general")
        summary = SkillRegistry._summarize_description(capability.description)
        return (
            f"{domain} > {capability.capability_type.value} > "
            f"{capability.name}: {summary}"
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
        domain: str | None = None,
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
            domain: Optional domain filter applied at the ChromaDB ``where``
                level — restricts the search to capabilities whose ``domain``
                metadata matches exactly. When ``None`` (default) the search
                is unrestricted, preserving the pre-B1 behaviour. This is the
                proposal's "hierarchical embeddings (domain → capability →
                specific skill)" criterion realised as a cheap pre-filter
                rather than a multi-level cascaded search. Used by the
                domain-filter experiment in Phase 2 Step 3.5 of the master
                plan; also available for production gap detection if the
                experiment shows a false-positive reduction.
        """
        query_kwargs: dict = {"query_texts": [query], "n_results": n_results}
        if domain is not None:
            query_kwargs["where"] = {"domain": {"$eq": domain}}
        results = self._collection.query(**query_kwargs)

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
        self,
        queries: list[str],
        *,
        threshold: float = 0.6,
        domain: str | None = None,
    ) -> tuple[list[tuple[CapabilityDescriptor, float, str]], list[tuple[str, str]]]:
        """For each query, search the registry and classify as match or gap.

        Args:
            queries: Required-capability query strings.
            threshold: Minimum similarity score to count as a match.
            domain: Optional domain filter threaded through to :meth:`search`.
                When ``None`` (default) the search is unrestricted. See
                :meth:`search` for rationale.

        Returns:
            matches: list of (capability, score, original_query) above threshold
            gaps: list of (query, reason) below threshold
        """
        matches: list[tuple[CapabilityDescriptor, float, str]] = []
        gaps: list[tuple[str, str]] = []

        for query in queries:
            # search() filters out is_active=False, so also query unfiltered
            # to detect deactivated capabilities and produce accurate gap reasons.
            results = self.search(query, n_results=1, domain=domain)
            unfiltered = self.search(
                query, n_results=1, is_active=None, domain=domain
            )

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
        input_schema: dict | None = None,
        output_schema: dict | None = None,
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
        if input_schema:
            kwargs["input_schema"] = input_schema
        if output_schema:
            kwargs["output_schema"] = output_schema
        return CapabilityDescriptor(**kwargs)

    seeds = [
        # MCP_TOOL type
        _cap(
            "fibonacci",
            "Compute the nth Fibonacci number",
            CapabilityType.MCP_TOOL,
            domain="mathematics",
            tags=["math", "computation", "sequence"],
            input_schema={"n": "int"},
            output_schema={"result": "int"},
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
            (
                "Parse a CSV file (comma-delimited, default csv.DictReader "
                "dialect) and return the row count and the list of column "
                "header names. Does not return row data and does not perform "
                "type coercion."
            ),
            CapabilityType.MCP_TOOL,
            domain="data-processing",
            tags=["csv", "parsing", "data-ingestion"],
            input_schema={"file_path": "str"},
            output_schema={"row_count": "int", "column_names": "list[str]"},
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
                "Reformat a date string from one strptime-compatible format "
                "to another strftime-compatible format. Input and output "
                "format strings are required parameters."
            ),
            CapabilityType.MCP_TOOL,
            domain="data-processing",
            tags=["date", "formatting", "conversion"],
            input_schema={
                "date_string": "str",
                "input_format": "str",
                "output_format": "str",
            },
            output_schema={"formatted_date": "str"},
            source_code=(
                'from typing import Annotated\n'
                'from datetime import datetime\n'
                'from mcp.server.fastmcp import FastMCP\n'
                'mcp = FastMCP("date-formatter")\n'
                '@mcp.tool()\n'
                'async def date_formatter(\n'
                '    date_string: Annotated[str, "Input date string"],\n'
                '    input_format: Annotated[str, "strptime-compatible input format"],\n'
                '    output_format: Annotated[str, "strftime-compatible output format"],\n'
                ') -> dict:\n'
                '    """Reformat a date string from one strptime format to another."""\n'
                '    dt = datetime.strptime(date_string, input_format)\n'
                '    return {"formatted_date": dt.strftime(output_format)}\n'
                'if __name__ == "__main__":\n'
                '    mcp.run()\n'
            ),
        ),
        _cap(
            "json-validator",
            (
                "Validate a JSON config file against a JSON Schema file "
                "(both provided as paths) using Draft-7, and return a dict "
                "with an `errors` key containing a list of error message "
                "strings. Does not return structured error paths or schema "
                "locations — only the message strings."
            ),
            CapabilityType.MCP_TOOL,
            domain="data-processing",
            tags=["json", "validation", "schema"],
            input_schema={"config_path": "str", "schema_path": "str"},
            output_schema={"errors": "list[str]"},
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
                "Compute the word count and Flesch reading-ease score for a "
                "plain-text document. Returns only `word_count` and "
                "`readability_score`."
            ),
            CapabilityType.MCP_TOOL,
            domain="text-analysis",
            tags=["text", "statistics", "nlp", "readability"],
            input_schema={"file_path": "str"},
            output_schema={"word_count": "int", "readability_score": "float"},
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
            input_schema={"data": "list[dict]"},
            output_schema={
                "clean_row_count": "int",
                "removed_nulls": "int",
                "removed_duplicates": "int",
                "outliers_flagged": "int",
            },
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
                "Generate an analysis report from a numeric dataset: "
                "compute summary statistics, identify the top 5 outlier "
                "records by z-score on a primary metric, and produce a "
                "one-paragraph executive summary. Returns a dict with "
                "`summary`, `stats_table`, and `anomalies`."
            ),
            CapabilityType.SKILL,
            domain="reporting",
            tags=["report", "summary", "documentation"],
            input_schema={"data": "list[dict]"},
            output_schema={
                "summary": "str",
                "stats_table": "dict",
                "anomalies": "list[dict]",
            },
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
            input_schema={"dataset_path": "str"},
            output_schema={
                "profile": "dict",
                "correlations": "dict",
                "insights": "list[str]",
            },
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
                '"description": "Autonomous EDA agent"}, '
                '"is_deep_agent": true}'
            ),
        ),
    ]
    for cap in seeds:
        registry.add(cap)
