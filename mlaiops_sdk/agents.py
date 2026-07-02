"""Agent infrastructure primitives.

Provides the platform-managed LangGraph checkpointer and the multi-tier
``AgentMemoryClient`` described in the PRD (section 4.6): semantic long-term
memory on pgvector and structured entity features from the online feature
gateway. Connection strings come from the environment (Kubernetes Secret
mounts in production), never from agent source code.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from typing import Any

import httpx

EMBEDDING_DIM = 256

_ENV_CHECKPOINT_DSN = "MLAIOPS_CHECKPOINT_DSN"
_ENV_MEMORY_DSN = "MLAIOPS_MEMORY_DSN"
_ENV_DATABASE_URL = "DATABASE_URL"
_ENV_FEATURE_GATEWAY = "MLAIOPS_FEATURE_GATEWAY_URL"

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


class MissingConfiguration(RuntimeError):
    """Raised when a required connection string is absent."""


def _database_dsn(primary_env: str) -> str:
    dsn = os.environ.get(primary_env) or os.environ.get(_ENV_DATABASE_URL)
    if not dsn:
        raise MissingConfiguration(
            f"set {primary_env} or {_ENV_DATABASE_URL} to the platform PostgreSQL DSN"
        )
    return dsn.replace("postgres://", "postgresql://", 1)


def get_langgraph_checkpointer(dsn: str | None = None):
    """Return the platform-managed ``AsyncPostgresSaver`` context manager.

    Usage::

        async with get_langgraph_checkpointer() as saver:
            await saver.setup()
            app = graph.compile(checkpointer=saver)
    """
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError as error:  # pragma: no cover - environment guard
        raise MissingConfiguration(
            "get_langgraph_checkpointer requires langgraph-checkpoint-postgres; "
            "install mlaiops-sdk[agents]"
        ) from error
    return AsyncPostgresSaver.from_conn_string(dsn or _database_dsn(_ENV_CHECKPOINT_DSN))


class HashingEmbedder:
    """Deterministic bag-of-words embedding.

    Same input, same vector, zero dependencies and zero network calls. Good
    enough for local semantic recall and exact for tests; swap in a provider
    embedding through ``AgentMemoryClient(embedder=...)`` when quality matters.
    """

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for token in _TOKEN_PATTERN.findall(text.lower()):
            digest = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]


class AgentMemoryClient:
    """Unified access to the agent memory tiers.

    - Long-term semantic memory: pgvector table ``agent_memories``.
    - Structured entity features: online feature gateway lookups.

    Short-term session state belongs to the LangGraph checkpointer and
    in-context state to the graph itself; this client covers the other tiers.
    """

    def __init__(
        self,
        agent_name: str,
        session_id: str = "",
        *,
        dsn: str | None = None,
        feature_gateway_url: str | None = None,
        embedder: Any | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.session_id = session_id
        self._dsn = dsn
        self._feature_gateway_url = feature_gateway_url or os.environ.get(_ENV_FEATURE_GATEWAY)
        self.embedder = embedder or HashingEmbedder()
        self._connection: Any = None

    # -- semantic long-term memory (pgvector) --------------------------------

    def _connect(self):
        if self._connection is not None and not self._connection.closed:
            return self._connection
        try:
            import psycopg
        except ImportError as error:  # pragma: no cover - environment guard
            raise MissingConfiguration(
                "semantic memory requires psycopg; install mlaiops-sdk[agents]"
            ) from error
        dsn = self._dsn or _database_dsn(_ENV_MEMORY_DSN)
        self._connection = psycopg.connect(dsn, autocommit=True)
        self._ensure_schema(self._connection)
        return self._connection

    def _ensure_schema(self, connection: Any) -> None:
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS agent_memories (
                    id BIGSERIAL PRIMARY KEY,
                    agent_name TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{{}}',
                    embedding vector({EMBEDDING_DIM}) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS agent_memories_agent_idx "
                "ON agent_memories (agent_name)"
            )

    @staticmethod
    def _vector_literal(embedding: list[float]) -> str:
        return "[" + ",".join(f"{value:.8f}" for value in embedding) + "]"

    def remember(self, text: str, *, metadata: dict[str, Any] | None = None) -> None:
        """Persist a fact in long-term semantic memory."""
        embedding = self._vector_literal(self.embedder.embed(text))
        with self._connect().cursor() as cursor:
            cursor.execute(
                "INSERT INTO agent_memories (agent_name, session_id, text, metadata, embedding)"
                " VALUES (%s, %s, %s, %s, %s::vector)",
                (
                    self.agent_name,
                    self.session_id,
                    text,
                    json.dumps(metadata or {}),
                    embedding,
                ),
            )

    def recall(self, query: str, *, top_k: int = 3) -> list[dict[str, Any]]:
        """Return the ``top_k`` most similar memories for this agent."""
        embedding = self._vector_literal(self.embedder.embed(query))
        with self._connect().cursor() as cursor:
            cursor.execute(
                "SELECT text, metadata, 1 - (embedding <=> %s::vector) AS score"
                " FROM agent_memories WHERE agent_name = %s"
                " ORDER BY embedding <=> %s::vector LIMIT %s",
                (embedding, self.agent_name, embedding, top_k),
            )
            rows = cursor.fetchall()
        return [
            {"text": text, "metadata": metadata, "score": float(score)}
            for text, metadata, score in rows
        ]

    # -- structured entity features (feature gateway) ------------------------

    def get_entity_features(
        self, entity_id: str, feature_view: str, *, timeout: float = 5.0
    ) -> dict[str, Any]:
        """Fetch real-time entity features from the online feature gateway."""
        if not self._feature_gateway_url:
            raise MissingConfiguration(f"set {_ENV_FEATURE_GATEWAY} to the feature gateway URL")
        response = httpx.post(
            f"{self._feature_gateway_url.rstrip('/')}/get-online-features",
            json={"feature_service": feature_view, "entities": [{"entity_id": entity_id}]},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        if self._connection is not None and not self._connection.closed:
            self._connection.close()

    def __enter__(self) -> "AgentMemoryClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
