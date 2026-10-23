import io
import json
import threading
from email.message import Message
from types import SimpleNamespace

import pytest

from kb.agent.tools.project_query import Project, ProjectArgs, query_projects
from kb.web import handler_class


def request(service, payload, origin=None, host="127.0.0.1:8765"):
    handler = object.__new__(handler_class(service))
    handler.path = "/api/query"
    handler.server = SimpleNamespace(server_port=8765)
    handler.headers = Message()
    handler.headers["Host"] = host
    handler.headers["Content-Type"] = "application/json"
    if origin:
        handler.headers["Origin"] = origin
    raw = json.dumps(payload).encode()
    handler.headers["Content-Length"] = str(len(raw))
    handler.rfile = io.BytesIO(raw)
    responses = []
    handler.send_json = lambda status, body: responses.append((status, body))
    handler.do_POST()
    return responses[0]


@pytest.mark.parametrize(
    "origin,host",
    [
        ("https://unrelated.example", "127.0.0.1:8765"),
        (None, "unrelated.example:8765"),
    ],
)
def test_reject_cross_origin_before_model_call(origin, host):
    assert request(None, {}, origin, host)[0] == 403


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"mode": "shell"},
        {"mode": "rag", "question": " "},
        {"mode": "agent", "question": "x", "session": "../other"},
        {"mode": "rag", "question": "x" * 2001, "session": "test"},
    ],
)
def test_invalid_request_does_not_invoke_model(payload):
    assert request(None, payload)[0] == 400


def test_busy_request_does_not_queue_or_mutate_session():
    lock = threading.Lock()
    lock.acquire()
    try:
        service = SimpleNamespace(lock=lock)
        assert request(service, {"mode": "rag", "question": "x", "session": "test"})[0] == 429
    finally:
        lock.release()


def test_api_errors_release_lock():
    def fail(*args):
        raise ValueError("需要新建会话")

    service = SimpleNamespace(lock=threading.Lock(), query=fail)
    status, body = request(service, {"mode": "agent", "question": "x", "session": "test"})
    assert status == 422 and body["error"] == "需要新建会话"
    assert not service.lock.locked()


def test_public_ledger_retains_source_with_selected_fields():
    project = Project(
        project_id="P",
        project_name="公开项目",
        signed_date="2026-04-10",
        amount="2861000.00",
        owner="未公开",
        supplier="公开供应商",
        source_url="https://example.org/public",
    )
    rows = query_projects([project], ProjectArgs(fields=["supplier"]), 80)
    assert rows == [
        {
            "project_name": "公开项目",
            "supplier": "公开供应商",
            "source_url": "https://example.org/public",
        }
    ]
