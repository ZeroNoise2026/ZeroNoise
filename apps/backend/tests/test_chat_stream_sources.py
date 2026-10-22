"""Smoke test: ChatbotUI's /api/chat/stream proxies the upstream `sources`
event to the client AND persists the items via append_chat_message.

Run from ChatbotUI/backend/:
    python -m tests.test_chat_stream_sources
"""
import os
import json

os.environ.setdefault("SUPABASE_URL", "http://stub")
os.environ.setdefault("SUPABASE_KEY", "stub")
os.environ.setdefault("QUESTION_SERVICE_URL", "http://stub:8003")

from fastapi.testclient import TestClient

import db as db_mod
import rag as rag_mod
import main as main_mod


client = TestClient(main_mod.app)


def _stub_db():
    db_mod.get_chat_session = lambda uid, sid: None  # force new session
    db_mod.create_chat_session = lambda uid, title: {"id": "sess-test"}
    db_mod.list_chat_sessions = lambda uid: []
    captured = {"appended": []}
    def fake_append(session_id, role, content, tickers=None, sources=None):
        captured["appended"].append({
            "session_id": session_id,
            "role": role,
            "content": content,
            "tickers": tickers,
            "sources": sources,
        })
        return 42
    db_mod.append_chat_message = fake_append
    return captured


def _stub_upstream_stream(events):
    """Replace rag.stream_answer_sse with a generator that yields the given
    SSE-formatted lines. Each event is one SSE 'data: ...\\n\\n' chunk."""
    def fake_stream(*_a, **_k):
        for e in events:
            yield e
        yield "data: [DONE]\n\n"
    rag_mod.stream_answer_sse = fake_stream


def test_sources_event_proxied_and_persisted():
    captured = _stub_db()

    sources_payload = [
        {"id": "doc:abc", "doc_type": "news", "ticker": "AAPL",
         "date": "2026-05-08", "title": "Apple beats Q2",
         "url": "https://example.com/aapl", "label": None, "similarity": 0.81},
        {"id": "table:earnings:AAPL:Q4 2025", "doc_type": "earnings_table",
         "ticker": "AAPL", "date": None, "title": None, "url": None,
         "label": "Supabase earnings table — AAPL", "similarity": None},
    ]
    upstream_events = [
        f"data: {json.dumps({'type':'token','text':'Hello '})}\n\n",
        f"data: {json.dumps({'type':'token','text':'world.'})}\n\n",
        f"data: {json.dumps({'type':'sources','items':sources_payload})}\n\n",
    ]
    _stub_upstream_stream(upstream_events)

    resp = client.post(
        "/api/chat/stream",
        json={"question": "How is AAPL?"},
        headers={"X-User-Id": "u-1"},
    )
    assert resp.status_code == 200
    body = resp.text

    # 1. Upstream sources event is forwarded to the client
    assert '"type": "sources"' in body or '"type":"sources"' in body
    assert "doc:abc" in body
    assert "table:earnings:AAPL:Q4 2025" in body

    # 2. The assistant message was persisted with sources attached
    assistant = [m for m in captured["appended"] if m["role"] == "assistant"]
    assert len(assistant) == 1
    persisted_sources = assistant[0]["sources"]
    assert isinstance(persisted_sources, list)
    assert len(persisted_sources) == 2
    assert persisted_sources[0]["id"] == "doc:abc"
    assert persisted_sources[1]["url"] is None
    assert assistant[0]["content"].strip() == "Hello world."
    print("sources event forwarded + persisted ✓")


def test_no_sources_event_persists_none():
    captured = _stub_db()
    upstream_events = [
        f"data: {json.dumps({'type':'token','text':'no citations here'})}\n\n",
    ]
    _stub_upstream_stream(upstream_events)

    resp = client.post(
        "/api/chat/stream",
        json={"question": "what?"},
        headers={"X-User-Id": "u-2"},
    )
    assert resp.status_code == 200
    assistant = [m for m in captured["appended"] if m["role"] == "assistant"]
    assert len(assistant) == 1
    # When upstream emits no `sources`, we pass sources=None to append.
    # Our append stub receives the kwarg as None.
    assert assistant[0]["sources"] is None
    print("no-sources case ✓ (NULL persisted)")


def main():
    test_sources_event_proxied_and_persisted()
    test_no_sources_event_persists_none()
    print("\nAll chat-stream sources tests passed.")


if __name__ == "__main__":
    main()
