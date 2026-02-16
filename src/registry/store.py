from __future__ import annotations

import json

import chromadb

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
            is_active: Filter by active status. None to include all.
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
    ) -> list[tuple[CapabilityDescriptor, float]]:
        """Semantic search. Returns (capability, score) pairs sorted by relevance.

        Score is cosine similarity (0-1, higher is better).

        Args:
            query: Natural-language search query.
            n_results: Maximum number of results to return.
            min_confidence: Minimum similarity score to include in results.
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
            pairs.append((descriptor, score))
        return pairs

    def detect_gaps(
        self, queries: list[str], *, threshold: float = 0.3
    ) -> tuple[list[tuple[CapabilityDescriptor, float, str]], list[tuple[str, str]]]:
        """For each query, search the registry and classify as match or gap.

        Returns:
            matches: list of (capability, score, original_query) above threshold
            gaps: list of (query, reason) below threshold
        """
        matches: list[tuple[CapabilityDescriptor, float, str]] = []
        gaps: list[tuple[str, str]] = []

        for query in queries:
            results = self.search(query, n_results=1)
            if results and results[0][1] >= threshold:
                cap, score = results[0]
                matches.append((cap, score, query))
            else:
                best_score = results[0][1] if results else 0.0
                gaps.append(
                    (
                        query,
                        f"best score {best_score:.2f} below threshold {threshold:.2f}",
                    )
                )

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
    ) -> CapabilityDescriptor:
        kwargs: dict = {
            "name": name,
            "description": description,
            "capability_type": capability_type,
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
        ),
        _cap(
            "csv-parser",
            "Parse a CSV file and return structured rows with headers and typed values",
            CapabilityType.MCP_TOOL,
            domain="data-processing",
            tags=["csv", "parsing", "data-ingestion"],
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
        ),
    ]
    for cap in seeds:
        registry.add(cap)
