from __future__ import annotations

import chromadb

from src.schemas.capability import CapabilityDescriptor, CapabilityType


class SkillRegistry:
    """ChromaDB-backed capability registry with semantic search."""

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

    def add(self, capability: CapabilityDescriptor) -> None:
        """Register a capability. Overwrites if the name already exists."""
        self._collection.upsert(
            ids=[capability.name],
            documents=[capability.description],
            metadatas=[
                {
                    "capability_type": capability.capability_type.value,
                    "version": capability.version,
                    "created_at": capability.created_at.isoformat(),
                }
            ],
        )

    def search(self, query: str, n_results: int = 3) -> list[tuple[CapabilityDescriptor, float]]:
        """Semantic search. Returns (capability, score) pairs sorted by relevance.

        Score is cosine similarity (0-1, higher is better).
        """
        results = self._collection.query(query_texts=[query], n_results=n_results)

        pairs: list[tuple[CapabilityDescriptor, float]] = []
        if not results["ids"] or not results["ids"][0]:
            return pairs

        for name, doc, meta, distance in zip(
            results["ids"][0],
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            score = 1.0 - distance  # cosine distance → similarity
            descriptor = CapabilityDescriptor(
                name=name,
                description=doc,
                capability_type=CapabilityType(meta["capability_type"]),
                version=meta["version"],
            )
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
                gaps.append((query, f"best score {best_score:.2f} below threshold {threshold:.2f}"))

        return matches, gaps

    @property
    def count(self) -> int:
        return self._collection.count()


def seed_registry(registry: SkillRegistry) -> None:
    """Seed with 3 hand-written capabilities for Iteration 0."""
    seeds = [
        CapabilityDescriptor(
            name="fibonacci",
            description="Compute the nth Fibonacci number",
            capability_type=CapabilityType.MCP_TOOL,
        ),
        CapabilityDescriptor(
            name="csv-parser",
            description="Parse a CSV file and return structured rows",
            capability_type=CapabilityType.MCP_TOOL,
        ),
        CapabilityDescriptor(
            name="date-formatter",
            description="Format a date string between different date formats",
            capability_type=CapabilityType.MCP_TOOL,
        ),
    ]
    for cap in seeds:
        registry.add(cap)
