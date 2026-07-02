"""Gate tests for agent memory primitives and the checkpointer contract.

Pure logic runs everywhere; the pgvector round-trip runs only when
``TEST_MEMORY_DSN`` points at a PostgreSQL instance with the vector extension
(the integration lane provides one).
"""

import os

import pytest

from mlaiops_sdk.agents import (
    EMBEDDING_DIM,
    AgentMemoryClient,
    HashingEmbedder,
    MissingConfiguration,
    get_langgraph_checkpointer,
)


def test_hashing_embedder_deterministic():
    embedder = HashingEmbedder()
    assert embedder.embed("user prefers email") == embedder.embed("user prefers email")


def test_hashing_embedder_normalized():
    vector = HashingEmbedder().embed("billing invoice monthly")
    assert len(vector) == EMBEDDING_DIM
    assert abs(sum(value * value for value in vector) - 1.0) < 1e-9


def test_hashing_embedder_similarity_orders_topics():
    embedder = HashingEmbedder()

    def cosine(a, b):
        return sum(x * y for x, y in zip(a, b))

    query = embedder.embed("email notification preferences")
    related = embedder.embed("user prefers email over sms notification")
    unrelated = embedder.embed("kubernetes cluster autoscaling policy")
    assert cosine(query, related) > cosine(query, unrelated)


def test_checkpointer_requires_dsn(monkeypatch):
    monkeypatch.delenv("MLAIOPS_CHECKPOINT_DSN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(MissingConfiguration, match="MLAIOPS_CHECKPOINT_DSN"):
        get_langgraph_checkpointer()


def test_memory_requires_feature_gateway_config():
    client = AgentMemoryClient("support")
    with pytest.raises(MissingConfiguration, match="MLAIOPS_FEATURE_GATEWAY_URL"):
        client.get_entity_features("u123", "customer_profile")


def test_vector_literal_format():
    literal = AgentMemoryClient._vector_literal([0.5, -0.25])
    assert literal == "[0.50000000,-0.25000000]"


@pytest.mark.skipif(
    not os.environ.get("TEST_MEMORY_DSN"),
    reason="TEST_MEMORY_DSN not set (integration lane only)",
)
def test_memory_round_trip_pgvector():
    with AgentMemoryClient("gate-test-agent", dsn=os.environ["TEST_MEMORY_DSN"]) as memory:
        memory.remember("User prefers email over SMS for notifications")
        memory.remember("Cluster upgrades happen on Tuesdays")
        results = memory.recall("how does the user want to be notified", top_k=1)
    assert len(results) == 1
    assert "email" in results[0]["text"]
