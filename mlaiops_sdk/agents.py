"""Agent infrastructure primitives.

Provides the platform-managed LangGraph checkpointer and the multi-tier
``AgentMemoryClient``: semantic long-term
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


# One complete query per recall scope. Every scope is bound to one agent and tenant.
_RECALL_SQL = {
    "session": (
        "SELECT text, metadata, 1 - (embedding <=> %s::vector) AS score FROM agent_memories"
        " WHERE tenant_id = %s AND agent_name = %s AND user_id = %s AND session_id = %s"
        " ORDER BY embedding <=> %s::vector LIMIT %s"
    ),
    "user": (
        "SELECT text, metadata, 1 - (embedding <=> %s::vector) AS score FROM agent_memories"
        " WHERE tenant_id = %s AND agent_name = %s AND user_id = %s"
        " ORDER BY embedding <=> %s::vector LIMIT %s"
    ),
    "agent": (
        "SELECT text, metadata, 1 - (embedding <=> %s::vector) AS score FROM agent_memories"
        " WHERE tenant_id = %s AND agent_name = %s"
        " ORDER BY embedding <=> %s::vector LIMIT %s"
    ),
}
_FORGET_USER_SQL = (
    "DELETE FROM agent_memories WHERE tenant_id = %s AND agent_name = %s AND user_id = %s"
)
_FORGET_SESSION_SQL = (
    "DELETE FROM agent_memories"
    " WHERE tenant_id = %s AND agent_name = %s AND user_id = %s AND session_id = %s"
)


class AgentMemoryClient:
    """Unified access to the agent memory tiers.

    - Long-term semantic memory: pgvector table ``agent_memories``.
    - Structured entity features: online feature gateway lookups.

    Short-term session state belongs to the LangGraph checkpointer and
    in-context state to the graph itself; this client covers the other tiers.

    Semantic memory is isolated per user. ``remember`` and ``recall`` need the
    authenticated ``user_id`` and raise ``MissingConfiguration`` without it, so
    one user's facts can never reach another. ``recall(scope="session")`` narrows
    to this session; ``scope="agent"`` reads across this agent's users and is an
    explicit opt-in. Never take ``user_id`` from model output or a request body.
    """

    def __init__(
        self,
        agent_name: str,
        session_id: str = "",
        *,
        user_id: str | None = None,
        tenant_id: str = "",
        dsn: str | None = None,
        feature_gateway_url: str | None = None,
        embedder: Any | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.session_id = session_id
        self.user_id = user_id
        self.tenant_id = tenant_id
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
                    tenant_id TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{{}}',
                    embedding vector({EMBEDDING_DIM}) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            # Tables from before user scoping keep their rows: those rows get an
            # empty user_id, so only scope="agent" can read them. Check first,
            # because ALTER TABLE takes an exclusive lock even when it does nothing.
            cursor.execute(
                "SELECT attname FROM pg_attribute WHERE attrelid = 'agent_memories'::regclass"
                " AND attname IN ('tenant_id', 'user_id') AND NOT attisdropped"
            )
            present = {row[0] for row in cursor.fetchall()}
            if "tenant_id" not in present:
                cursor.execute(
                    "ALTER TABLE agent_memories"
                    " ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT ''"
                )
            if "user_id" not in present:
                cursor.execute(
                    "ALTER TABLE agent_memories"
                    " ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT ''"
                )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS agent_memories_agent_idx "
                "ON agent_memories (agent_name)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS agent_memories_scope_idx "
                "ON agent_memories (tenant_id, agent_name, user_id, session_id)"
            )

    @staticmethod
    def _vector_literal(embedding: list[float]) -> str:
        return "[" + ",".join(f"{value:.8f}" for value in embedding) + "]"

    def _require_user(self, action: str) -> str:
        if not (self.user_id or "").strip():
            raise MissingConfiguration(
                f"{action} needs the authenticated user: pass "
                'AgentMemoryClient(..., user_id=...), or scope="agent" for agent-wide memory'
            )
        return self.user_id

    def _require_session(self, action: str) -> str:
        if not self.session_id.strip():
            raise MissingConfiguration(
                f'{action} with scope="session" needs AgentMemoryClient(..., session_id=...)'
            )
        return self.session_id

    def _scope_parameters(self, scope: str, action: str) -> tuple[str, ...]:
        """The filter values for ``_RECALL_SQL[scope]``, failing closed on a missing identity."""
        if scope not in _RECALL_SQL:
            raise ValueError(f"unknown memory scope {scope!r}; use session, user or agent")
        if scope == "agent":
            return (self.tenant_id, self.agent_name)
        user_id = self._require_user(action)
        if scope == "user":
            return (self.tenant_id, self.agent_name, user_id)
        return (self.tenant_id, self.agent_name, user_id, self._require_session(action))

    def remember(
        self, text: str, *, metadata: dict[str, Any] | None = None, scope: str = "user"
    ) -> None:
        """Persist a fact in long-term semantic memory.

        The fact belongs to this user and session. ``scope="agent"`` stores a
        fact with no owner, which only ``recall(scope="agent")`` can read.
        """
        self._scope_parameters(scope, "remember")
        user_id = "" if scope == "agent" else self.user_id
        session_id = "" if scope == "agent" else self.session_id
        embedding = self._vector_literal(self.embedder.embed(text))
        with self._connect().cursor() as cursor:
            cursor.execute(
                "INSERT INTO agent_memories"
                " (agent_name, tenant_id, user_id, session_id, text, metadata, embedding)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s::vector)",
                (
                    self.agent_name,
                    self.tenant_id,
                    user_id,
                    session_id,
                    text,
                    json.dumps(metadata or {}),
                    embedding,
                ),
            )

    def recall(self, query: str, *, top_k: int = 3, scope: str = "user") -> list[dict[str, Any]]:
        """Return the ``top_k`` most similar memories visible in ``scope``.

        ``user`` (default) reads this user's memories, ``session`` narrows to this
        session, and ``agent`` reads every memory of this agent and tenant.
        """
        parameters = self._scope_parameters(scope, "recall")
        embedding = self._vector_literal(self.embedder.embed(query))
        with self._connect().cursor() as cursor:
            cursor.execute(
                _RECALL_SQL[scope],
                (embedding, *parameters, embedding, top_k),
            )
            rows = cursor.fetchall()
        return [
            {"text": text, "metadata": metadata, "score": float(score)}
            for text, metadata, score in rows
        ]

    def forget(self, *, user_id: str | None = None, session_id: str | None = None) -> int:
        """Delete one user's memories of this agent and tenant; return the row count.

        ``user_id`` defaults to this client's user. A client bound to a user can
        only delete that user's rows; an unbound client serves deletion requests
        for any named user. ``session_id`` narrows the deletion to one session.
        Other agents keep their rows: run it once per agent for a full erasure.
        """
        target = user_id if user_id is not None else self.user_id
        if not (target or "").strip():
            raise MissingConfiguration("forget needs the user_id whose memories to delete")
        if self.user_id and target != self.user_id:
            raise ValueError("this client is bound to another user; use an unbound client")
        parameters = (self.tenant_id, self.agent_name, target)
        with self._connect().cursor() as cursor:
            if session_id is None:
                cursor.execute(_FORGET_USER_SQL, parameters)
            else:
                cursor.execute(_FORGET_SESSION_SQL, (*parameters, session_id))
            return cursor.rowcount

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
