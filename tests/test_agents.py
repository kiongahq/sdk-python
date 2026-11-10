"""Gate tests for agent memory primitives and the checkpointer contract.

Pure logic runs everywhere; the pgvector round-trip runs only when
``TEST_MEMORY_DSN`` points at a PostgreSQL instance with the vector extension
(the integration lane provides one).
"""

import os
import uuid

import pytest

from kionga.agents import (
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
    monkeypatch.delenv("KIONGA_CHECKPOINT_DSN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(MissingConfiguration, match="KIONGA_CHECKPOINT_DSN"):
        get_langgraph_checkpointer()


def test_memory_requires_feature_gateway_config():
    client = AgentMemoryClient("support")
    with pytest.raises(MissingConfiguration, match="KIONGA_FEATURE_GATEWAY_URL"):
        client.get_entity_features("u123", "customer_profile")


def test_vector_literal_format():
    literal = AgentMemoryClient._vector_literal([0.5, -0.25])
    assert literal == "[0.50000000,-0.25000000]"


class RecordingCursor:
    """Fails the test if identity checks let a query reach the database."""

    def __enter__(self):
        raise AssertionError("query reached the database")

    def __exit__(self, *_):
        return False


class RecordingConnection:
    closed = False

    def cursor(self):
        return RecordingCursor()


def unconnected(*args, **kwargs):
    client = AgentMemoryClient(*args, **kwargs)
    client._connection = RecordingConnection()
    return client


@pytest.mark.parametrize("user_id", [None, "", "   "])
def test_memory_fails_closed_without_a_user(user_id):
    client = unconnected("support", "s1", user_id=user_id)
    for action in (lambda: client.remember("fact"), lambda: client.recall("fact"),
                   lambda: client.recall("fact", scope="session"), lambda: client.forget()):
        with pytest.raises(MissingConfiguration, match="user"):
            action()


@pytest.mark.parametrize("session_id", ["", "  "])
def test_session_scope_needs_a_session(session_id):
    client = unconnected("support", session_id, user_id="alice")
    with pytest.raises(MissingConfiguration, match="session_id"):
        client.recall("fact", scope="session")


def test_unknown_scope_is_rejected():
    client = unconnected("support", user_id="alice")
    with pytest.raises(ValueError, match="unknown memory scope"):
        client.recall("fact", scope="everyone")


def test_forget_never_runs_with_a_blank_target():
    # A blank user_id matches every pre-scoping row, so it must never reach SQL.
    client = unconnected("support")
    for target in ("", "  ", None):
        with pytest.raises(MissingConfiguration):
            client.forget(user_id=target)


def test_bound_client_cannot_forget_another_user():
    with pytest.raises(ValueError, match="another user"):
        unconnected("support", user_id="alice").forget(user_id="bob")


pgvector = pytest.mark.skipif(
    not os.environ.get("TEST_MEMORY_DSN"),
    reason="TEST_MEMORY_DSN not set (integration lane only)",
)


@pytest.fixture
def agent_name():
    # A fresh agent per test keeps rows from other tests out of every scope.
    return f"gate-{uuid.uuid4().hex}"


def memory_for(agent, user_id=None, session_id="", **kwargs):
    return AgentMemoryClient(agent, session_id, user_id=user_id, dsn=os.environ["TEST_MEMORY_DSN"], **kwargs)


def texts(results):
    return {item["text"] for item in results}


@pgvector
def test_memory_round_trip_pgvector(agent_name):
    with memory_for(agent_name, "alice") as memory:
        memory.remember("User prefers email over SMS for notifications")
        memory.remember("Cluster upgrades happen on Tuesdays")
        results = memory.recall("how does the user want to be notified", top_k=1)
    assert len(results) == 1
    assert "email" in results[0]["text"]


@pgvector
def test_pgvector_memory_of_one_user_never_reaches_another(agent_name):
    with memory_for(agent_name, "alice") as alice, memory_for(agent_name, "bob") as bob:
        alice.remember("alice prefers email")
        bob.remember("bob prefers phone")
        assert texts(alice.recall("prefers", top_k=10)) == {"alice prefers email"}
        assert texts(bob.recall("prefers", top_k=10)) == {"bob prefers phone"}


@pgvector
def test_pgvector_same_session_id_does_not_cross_users(agent_name):
    with memory_for(agent_name, "alice", "shared") as alice, memory_for(agent_name, "bob", "shared") as bob:
        alice.remember("alice note")
        assert bob.recall("note", top_k=10, scope="session") == []


@pgvector
def test_pgvector_session_scope_returns_only_this_session(agent_name):
    with memory_for(agent_name, "alice", "s1") as first, memory_for(agent_name, "alice", "s2") as second:
        first.remember("note from the first session")
        second.remember("note from the second session")
        assert texts(first.recall("note", top_k=10, scope="session")) == {"note from the first session"}
        assert texts(first.recall("note", top_k=10)) == {"note from the first session", "note from the second session"}


@pgvector
def test_pgvector_agent_scope_is_explicit_and_stays_in_the_agent_and_tenant(agent_name):
    other_agent = f"{agent_name}-other"
    with memory_for(agent_name, "alice") as alice, memory_for(agent_name) as operator, \
            memory_for(other_agent, "alice") as elsewhere, memory_for(agent_name, "carol", tenant_id="t2") as tenant:
        alice.remember("alice note")
        operator.remember("agent-wide note", scope="agent")
        elsewhere.remember("other agent note")
        tenant.remember("other tenant note")
        assert texts(operator.recall("note", top_k=10, scope="agent")) == {"alice note", "agent-wide note"}
        assert texts(alice.recall("note", top_k=10)) == {"alice note"}  # no agent-wide rows by default


@pgvector
def test_pgvector_tenants_are_isolated_for_the_same_user(agent_name):
    with memory_for(agent_name, "alice", tenant_id="t1") as first, memory_for(agent_name, "alice", tenant_id="t2") as second:
        first.remember("tenant one note")
        assert second.recall("note", top_k=10) == []


@pgvector
def test_pgvector_forget_deletes_only_that_user(agent_name):
    with memory_for(agent_name, "alice", "s1") as alice, memory_for(agent_name, "bob") as bob, memory_for(agent_name) as admin:
        alice.remember("alice one")
        alice.remember("alice two")
        bob.remember("bob one")
        assert admin.forget(user_id="alice") == 2
        assert alice.recall("alice", top_k=10) == []
        assert texts(bob.recall("bob", top_k=10)) == {"bob one"}


@pgvector
def test_pgvector_forget_can_target_one_session(agent_name):
    with memory_for(agent_name, "alice", "s1") as first, memory_for(agent_name, "alice", "s2") as second:
        first.remember("first note")
        second.remember("second note")
        assert first.forget(session_id="s1") == 1
        assert texts(second.recall("note", top_k=10)) == {"second note"}


@pgvector
def test_pgvector_schema_upgrade_keeps_existing_rows_and_hides_them_from_users():
    # A private schema holds the old table, so other tests' rows are untouched.
    import psycopg

    schema = f"legacy_{uuid.uuid4().hex}"
    base = os.environ["TEST_MEMORY_DSN"]
    dsn = f"{base}{'&' if '?' in base else '?'}options=-csearch_path%3D{schema},public"
    vector = AgentMemoryClient._vector_literal(HashingEmbedder().embed("legacy fact"))
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
        admin.execute(f"CREATE SCHEMA {schema}")
        admin.execute(
            f"""CREATE TABLE {schema}.agent_memories (
                id BIGSERIAL PRIMARY KEY, agent_name TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '', text TEXT NOT NULL,
                metadata JSONB NOT NULL DEFAULT '{{}}',
                embedding vector({EMBEDDING_DIM}) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
        )
        admin.execute(
            f"INSERT INTO {schema}.agent_memories (agent_name, session_id, text, embedding)"
            " VALUES ('legacy', 's0', 'legacy fact', %s::vector)", (vector,)
        )
    try:
        with AgentMemoryClient("legacy", user_id="alice", dsn=dsn) as memory:
            assert memory.recall("legacy fact", top_k=10) == []  # fails closed
            assert texts(memory.recall("legacy fact", top_k=10, scope="agent")) == {"legacy fact"}  # not lost
            memory.remember("legacy fact from alice")
            assert texts(memory.recall("legacy fact", top_k=10)) == {"legacy fact from alice"}
            assert memory.forget() == 1  # the unowned row is not alice's to delete
            assert texts(memory.recall("legacy fact", top_k=10, scope="agent")) == {"legacy fact"}
        with AgentMemoryClient("legacy", user_id="alice", dsn=dsn) as again:  # idempotent
            assert again.recall("legacy fact", top_k=10) == []
        with psycopg.connect(base, autocommit=True) as admin:
            indexes = {row[0] for row in admin.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = %s", (schema,)).fetchall()}
        assert "agent_memories_scope_idx" in indexes
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")
