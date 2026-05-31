from __future__ import annotations

import json
from datetime import UTC, datetime

import chromadb

from src.config import DEFAULT_LLM_MODEL
from src.schemas.capability import CapabilityDescriptor, CapabilityType


class CapabilityRegistry:
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

        The 50-word cap keeps typical embedding documents compact while
        preserving enough prose signal for seed retrieval. Shorter caps
        reduced recall in retrieval probes.
        """
        words = description.split()
        if len(words) <= max_words:
            return description
        return " ".join(words[:max_words]) + "…"

    @staticmethod
    def _build_document(capability: CapabilityDescriptor) -> str:
        """Build hierarchical embedding document for richer semantic search.

        Format: "{domain} > {capability_type} > {name}: {summary}"

        The description is truncated to ~50 words for embedding efficiency;
        the full description is kept in metadata (``raw_description``).

        The embedding text intentionally stays prose-only. Structural
        contract fields are persisted as ``input_schema`` / ``output_schema``
        metadata and consumed directly by merge scoring and gap-detection
        compatibility checks. Keeping schema keys out of the embedding avoids
        diluting semantic retrieval while preserving the contract signal for
        code paths that need it.
        """
        domain = getattr(capability, "domain", "general")
        summary = CapabilityRegistry._summarize_description(capability.description)
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
            # Optional descriptor fields are read defensively for compatibility
            # with older persisted rows and test descriptors.
            "domain": getattr(capability, "domain", "general"),
            "is_generic": getattr(capability, "is_generic", False),
            "is_active": getattr(capability, "is_active", True),
            "definition_token_cost": int(
                getattr(capability, "definition_token_cost", 0)
            ),
            "tags": ",".join(getattr(capability, "tags", [])),
            "input_schema": json.dumps(getattr(capability, "input_schema", {})),
            "output_schema": json.dumps(getattr(capability, "output_schema", {})),
            "pip_packages": json.dumps(
                list(getattr(capability, "pip_packages", []) or [])
            ),
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

        # Conditionally add fields the schema supports.
        if "domain" in model_fields and "domain" in meta:
            kwargs["domain"] = meta["domain"]
        if "is_generic" in model_fields and "is_generic" in meta:
            kwargs["is_generic"] = meta["is_generic"]
        if "is_active" in model_fields and "is_active" in meta:
            kwargs["is_active"] = meta["is_active"]
        if (
            "definition_token_cost" in model_fields
            and "definition_token_cost" in meta
        ):
            try:
                kwargs["definition_token_cost"] = int(meta["definition_token_cost"])
            except (ValueError, TypeError):
                kwargs["definition_token_cost"] = 0
        if "tags" in model_fields and "tags" in meta:
            raw_tags = meta["tags"]
            kwargs["tags"] = [t for t in raw_tags.split(",") if t] if raw_tags else []
        if "input_schema" in model_fields and "input_schema" in meta:
            kwargs["input_schema"] = json.loads(meta["input_schema"])
        if "output_schema" in model_fields and "output_schema" in meta:
            kwargs["output_schema"] = json.loads(meta["output_schema"])
        if "pip_packages" in model_fields and "pip_packages" in meta:
            try:
                raw_pp = json.loads(meta["pip_packages"])
                kwargs["pip_packages"] = (
                    [str(p) for p in raw_pp] if isinstance(raw_pp, list) else []
                )
            except (ValueError, TypeError):
                kwargs["pip_packages"] = []
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
            is_active: Filter by active status. The default returns active
                capabilities only; pass ``None`` to include all.
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
        include_generic: bool = True,
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
            domain: Optional domain filter. When set, restricts results to
                capabilities whose ``domain`` metadata matches (with the
                ``include_generic`` exception below). When ``None`` (default)
                no domain filtering is applied. This realizes hierarchical
                retrieval as a cheap pre-filter.
            include_generic: When ``True`` (default), capabilities flagged
                ``is_generic=True`` are always included in results regardless
                of the ``domain`` filter. This handles the generic-capability
                edge case where a genuinely generic cap like
                ``parse_csv_and_return_stats`` would be falsely rejected by
                strict domain-equality filtering on business-domain queries.
                Set ``False`` to disable the bypass for diagnostic
                measurements. Has no effect when ``domain`` is ``None``.
        """
        query_kwargs: dict = {"query_texts": [query], "n_results": n_results}
        if domain is not None:
            if include_generic:
                # Include caps matching the domain OR flagged as generic.
                # ChromaDB's where clause supports $or over equality checks.
                query_kwargs["where"] = {
                    "$or": [
                        {"domain": {"$eq": domain}},
                        {"is_generic": {"$eq": True}},
                    ]
                }
            else:
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
        auxiliary_queries: list[str | None] | None = None,
        threshold: float = 0.6,
        domain: str | None = None,
        include_generic: bool = True,
    ) -> tuple[list[tuple[CapabilityDescriptor, float, str]], list[tuple[str, str]]]:
        """For each query, search the registry and classify as match or gap.

        Args:
            queries: Required-capability query strings (typically short
                hyphen-case slugs produced by the decomposer).
            auxiliary_queries: Optional per-query auxiliary search strings
                (typically the natural-language ``SubTask.description``
                produced alongside each slug). When provided, ``detect_gaps``
                runs TWO searches per gap — one on the primary slug, one on
                the auxiliary description — and takes the MAX score for the
                threshold check. Strictly additive: setting an auxiliary to
                None or passing ``auxiliary_queries=None`` reproduces the
                single-query behaviour exactly. Use this to work
                around the short-slug-vs-prose-document embedding asymmetry
                where short slugs systematically fall below the 0.6
                threshold against prose-style ``_build_document()`` output
                even when semantically equivalent.
            threshold: Minimum similarity score to count as a match.
            domain: Optional domain filter threaded through to :meth:`search`.
                When ``None`` (default) the search is unrestricted. See
                :meth:`search` for rationale.
            include_generic: Threaded through to :meth:`search`. When True
                (default), capabilities flagged ``is_generic=True`` bypass
                the domain filter. See :meth:`search` docstring for the
                generic-capability rationale.

        Returns:
            matches: list of (capability, score, original_query) above threshold.
                The score reflects whichever of primary/auxiliary won the
                max comparison; ``original_query`` is always the primary
                (slug) for attribution continuity with metrics and traces.
            gaps: list of (query, reason) below threshold. The reason string
                shows both primary and auxiliary scores for debugging.
        """
        matches: list[tuple[CapabilityDescriptor, float, str]] = []
        gaps: list[tuple[str, str]] = []

        # Normalize auxiliary_queries to the same length as queries. None
        # entries are treated as "no auxiliary — single-query behaviour".
        if auxiliary_queries is None:
            aux_list: list[str | None] = [None] * len(queries)
        else:
            if len(auxiliary_queries) != len(queries):
                raise ValueError(
                    f"auxiliary_queries length ({len(auxiliary_queries)}) "
                    f"must match queries length ({len(queries)})"
                )
            aux_list = list(auxiliary_queries)

        for query, auxiliary in zip(queries, aux_list):
            # Primary search on the slug — search() filters out is_active=False,
            # so also query unfiltered to detect deactivated capabilities and
            # produce accurate gap reasons.
            primary_results = self.search(
                query, n_results=1, domain=domain, include_generic=include_generic
            )
            unfiltered = self.search(
                query,
                n_results=1,
                is_active=None,
                domain=domain,
                include_generic=include_generic,
            )
            best_primary_score = primary_results[0][1] if primary_results else 0.0
            best_primary_cap = primary_results[0][0] if primary_results else None

            # Auxiliary search on the description (if provided) — same filter
            # semantics, independent search.
            best_aux_score = 0.0
            best_aux_cap: CapabilityDescriptor | None = None
            if auxiliary and auxiliary.strip():
                aux_results = self.search(
                    auxiliary,
                    n_results=1,
                    domain=domain,
                    include_generic=include_generic,
                )
                if aux_results:
                    best_aux_cap, best_aux_score = aux_results[0]

            # Take the max-scoring hit. On ties, primary wins (slug
            # attribution preserved in the match tuple for metrics continuity).
            if best_primary_score >= best_aux_score:
                best_cap = best_primary_cap
                best_score = best_primary_score
            else:
                best_cap = best_aux_cap
                best_score = best_aux_score

            if best_cap is not None and best_score >= threshold:
                matches.append((best_cap, best_score, query))
            else:
                # The deactivated check only uses the primary slug query.
                # Adding auxiliary searches would add noise without changing
                # matching outcomes.
                if (
                    unfiltered
                    and unfiltered[0][1] >= threshold
                    and not unfiltered[0][0].is_active
                ):
                    reason = f"capability '{unfiltered[0][0].name}' is deactivated"
                elif auxiliary and auxiliary.strip():
                    reason = (
                        f"best score {best_score:.2f} below threshold "
                        f"{threshold:.2f} (primary {best_primary_score:.2f}, "
                        f"auxiliary {best_aux_score:.2f})"
                    )
                else:
                    reason = (
                        f"best score {best_primary_score:.2f} below threshold "
                        f"{threshold:.2f}"
                    )
                gaps.append((query, reason))

        return matches, gaps

    @property
    def count(self) -> int:
        return self._collection.count()


# Backwards-compatible alias for older scripts and tests.
SkillRegistry = CapabilityRegistry


def seed_registry(registry: CapabilityRegistry) -> None:
    """Seed with diverse capabilities spanning all three types."""
    # Local imports to avoid circular deps at module import time.
    from src.registry.token_count import count_definition_tokens
    from src.schemas.task import GeneratedArtifact

    # Optional fields are only passed when the schema supports them.
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
        pip_packages: list[str] | None = None,
    ) -> CapabilityDescriptor:
        metadata: dict = {"pinned": "true"}  # all seeded caps are pinned
        if source_code is not None:
            metadata["source_code"] = source_code
        if skill_content is not None:
            metadata["skill_content"] = skill_content
        if agent_config is not None:
            metadata["agent_config"] = agent_config

        # Compute the definition-string token cost for seeded capabilities
        # using the same serializer as generated artifacts. This keeps seeded
        # and evolved capabilities comparable in evaluation analysis.
        from src.schemas.task import decode_agent_config_metadata

        agent_config_dict = decode_agent_config_metadata(agent_config)
        if agent_config_dict is None and isinstance(agent_config, str) and agent_config:
            # Tokenizer snapshots can represent non-JSON agent prompts as
            # bare system prompts.
            agent_config_dict = {"system_prompt": agent_config}
        snapshot = GeneratedArtifact(
            capability_name=name,
            capability_type=capability_type,
            description=description,
            source_code=source_code,
            skill_content=skill_content,
            agent_config=agent_config_dict,
            input_schema=dict(input_schema or {}),
            output_schema=dict(output_schema or {}),
        )
        definition_token_cost = count_definition_tokens(snapshot)

        kwargs: dict = {
            "name": name,
            "description": description,
            "capability_type": capability_type,
            "definition_token_cost": definition_token_cost,
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
        if pip_packages:
            kwargs["pip_packages"] = list(pip_packages)
        return CapabilityDescriptor(**kwargs)

    seeds = [
        # MCP_TOOL type
        _cap(
            "fibonacci",
            "Compute the nth Fibonacci number",
            CapabilityType.MCP_TOOL,
            domain="mathematics",
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
                '        return {"row_count": len(rows), '
                '"column_names": list(reader.fieldnames or [])}\n'
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
            input_schema={"config_path": "str", "schema_path": "str"},
            output_schema={"errors": "list[str]"},
            # Declare the jsonschema runtime dependency explicitly so
            # registry-matched uses of this seed install it in the sandbox.
            pip_packages=["jsonschema"],
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
            input_schema={"file_path": "str"},
            output_schema={"word_count": "int", "readability_score": "float"},
            source_code=(
                'from typing import Annotated\n'
                'from mcp.server.fastmcp import FastMCP\n'
                'import re\n'
                'mcp = FastMCP("text-statistics")\n'
                '@mcp.tool()\n'
                'async def text_statistics(\n'
                '    file_path: Annotated[str, "Path to text file"]\n'
                ') -> dict:\n'
                '    """Compute word count and Flesch readability score."""\n'
                '    text = open(file_path).read()\n'
                '    words = re.findall(r"\\b\\w+\\b", text)\n'
                '    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]\n'
                '    def syllables(w):\n'
                '        return max(len(re.findall(r"[aeiouAEIOU]+", w)), 1)\n'
                '    n_w = len(words)\n'
                '    n_s = max(len(sentences), 1)\n'
                '    n_syl = sum(syllables(w) for w in words)\n'
                '    raw_score = 206.835 - 1.015*(n_w/n_s) - 84.6*(n_syl/max(n_w, 1))\n'
                '    score = round(max(0.0, min(100.0, raw_score)), 2)\n'
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
        _cap(
            "inventory-reorder-policy",
            (
                "Apply a fixed inventory reorder workflow to stock data using "
                "reorder thresholds and capped order values from a YAML policy. "
                "Returns product_count, items_below_threshold, and total_reorder_cost."
            ),
            CapabilityType.SKILL,
            domain="inventory-management",
            input_schema={"inventory_csv_path": "str", "rules_yaml_path": "str"},
            output_schema={
                "product_count": "int",
                "items_below_threshold": "int",
                "total_reorder_cost": "float",
            },
            skill_content=(
                "---\n"
                "name: inventory-reorder-policy\n"
                "description: Apply a deterministic reorder policy to inventory data\n"
                "---\n\n"
                "# Inventory Reorder Policy\n\n"
                "1. Load inventory rows and reorder rules.\n"
                "2. Compute days_of_stock = current_stock / daily_usage.\n"
                "3. Mark items below min_days_of_stock for reorder.\n"
                "4. Compute reorder_qty = ceil(daily_usage * reorder_days_supply).\n"
                "5. Cap order_cost at max_order_value and sum the total.\n"
            ),
        ),
        _cap(
            "sales-integration-report",
            (
                "Integrate sales CSV data with a product catalog and target table, "
                "then emit a regional revenue summary as an Excel workbook."
            ),
            CapabilityType.SKILL,
            domain="sales-analytics",
            input_schema={
                "sales_csv_path": "str",
                "products_xlsx_path": "str",
                "targets_csv_path": "str",
            },
            output_schema={
                "summary_row_count": "int",
                "region_revenues": "dict",
                "regions_meeting_target": "int",
                "output_file": "str",
            },
            skill_content=(
                "---\n"
                "name: sales-integration-report\n"
                "description: Join sales, products, and targets into a regional summary workbook\n"
                "---\n\n"
                "# Sales Integration Report\n\n"
                "1. Parse sales CSV and product catalog workbook.\n"
                "2. Join on product_id.\n"
                "3. Aggregate quantity * unit_price by region.\n"
                "4. Compare each region against its target.\n"
                "5. Write an Excel summary with region, revenue, target, and meets_target.\n"
            ),
        ),
        _cap(
            "budget-allocation-policy",
            (
                "Aggregate departmental transactions, compare the totals against "
                "budget limits, and count flagged transactions over a fixed threshold."
            ),
            CapabilityType.SKILL,
            domain="budget-reconciliation",
            input_schema={
                "departments_csv_path": "str",
                "transactions_tsv_path": "str",
                "rules_json_path": "str",
            },
            output_schema={
                "department_count": "int",
                "department_spending": "dict",
                "over_budget_count": "int",
                "flagged_transactions": "int",
            },
            skill_content=(
                "---\n"
                "name: budget-allocation-policy\n"
                "description: Reconcile spending against budget policy thresholds\n"
                "---\n\n"
                "# Budget Allocation Policy\n\n"
                "1. Load department budgets and transactions.\n"
                "2. Sum transaction amounts per department.\n"
                "3. Flag departments above annual budget.\n"
                "4. Count transactions above max_single_transaction.\n"
            ),
        ),
        _cap(
            "monitoring-aggregation-policy",
            (
                "Aggregate hourly monitoring data into daily metrics using a "
                "JSON policy and count alert days where thresholds are exceeded."
            ),
            CapabilityType.SKILL,
            domain="monitoring",
            input_schema={"hourly_csv_path": "str", "config_json_path": "str"},
            output_schema={
                "n_days": "int",
                "metrics_aggregated": "int",
                "alert_days": "int",
            },
            skill_content=(
                "---\n"
                "name: monitoring-aggregation-policy\n"
                "description: Apply daily monitoring aggregations and alert thresholds\n"
                "---\n\n"
                "# Monitoring Aggregation Policy\n\n"
                "1. Group hourly metric rows by day.\n"
                "2. Apply configured aggregations per metric.\n"
                "3. Count days where any aggregated metric breaches a threshold.\n"
            ),
        ),
        _cap(
            "eligibility-policy-classifier",
            (
                "Apply a fixed eligibility-rule set to application rows and "
                "return eligible/ineligible counts plus the number of rules applied."
            ),
            CapabilityType.SKILL,
            domain="eligibility",
            input_schema={"applications_csv_path": "str", "policy_json_path": "str"},
            output_schema={
                "total_applications": "int",
                "eligible_count": "int",
                "ineligible_count": "int",
                "rules_applied": "int",
            },
            skill_content=(
                "---\n"
                "name: eligibility-policy-classifier\n"
                "description: Classify applications using a deterministic rule set\n"
                "---\n\n"
                "# Eligibility Policy Classifier\n\n"
                "1. Load the policy JSON and application rows.\n"
                "2. Apply every rule to every row.\n"
                "3. Count eligible and ineligible applications.\n"
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
        _cap(
            "anomaly-investigation-agent",
            (
                "Investigate time-series metrics against declared baselines, "
                "identify every out-of-range point, and summarize anomalies."
            ),
            CapabilityType.AGENT,
            domain="monitoring",
            input_schema={"metrics_csv_path": "str", "baselines_json_path": "str"},
            output_schema={
                "anomaly_count": "int",
                "anomalies": "list[dict]",
                "metrics_analyzed": "int",
            },
            agent_config=(
                '{"system_prompt": "You investigate monitoring anomalies by reading '
                'metrics and baseline files, identifying out-of-range values, and '
                'returning a structured anomaly list.", '
                '"node_config": {"name": "anomaly_investigation_node", '
                f'"model": "{DEFAULT_LLM_MODEL}", '
                '"description": "Deterministic anomaly investigation agent"}, '
                '"is_deep_agent": true}'
            ),
        ),
    ]
    for cap in seeds:
        registry.add(cap)
