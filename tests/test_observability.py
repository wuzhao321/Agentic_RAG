import pytest

from kb.observability import tracing_settings


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.smith.langchain.com",
        "https://api.smith.langchain.com.example.com",
        "https://user:pass@api.smith.langchain.com",
        "https://api.smith.langchain.com/path",
        "https://api.smith.langchain.com?key=secret",
        "https://api.smith.langchain.com#fragment",
        "https://api.smith.langchain.com:8443",
    ],
)
def test_rejects_unapproved_credential_destinations(endpoint):
    with pytest.raises(ValueError, match="官方区域"):
        tracing_settings({"LANGSMITH_ENDPOINT": endpoint, "LANGSMITH_API_KEY": "test-key"})


def test_tracing_requires_separate_key():
    with pytest.raises(ValueError, match="LANGSMITH_API_KEY"):
        tracing_settings({"DEEPSEEK_API_KEY": "not-a-langsmith-key"})


def test_regional_workspace_settings():
    settings = tracing_settings(
        {
            "LANGSMITH_API_KEY": "test-key",
            "LANGSMITH_ENDPOINT": "https://eu.api.smith.langchain.com/",
            "LANGSMITH_WORKSPACE_ID": "workspace-id",
        }
    )
    assert settings["api_url"] == "https://eu.api.smith.langchain.com"
    assert settings["workspace_id"] == "workspace-id"
    assert settings["project_name"] == "kb-rag-agent"


def test_disabled_tracing_does_not_require_key_or_create_client(monkeypatch):
    import langsmith

    from kb import observability

    def forbidden(*args, **kwargs):
        raise AssertionError("Tracing is disabled; no cloud client may be constructed")

    monkeypatch.setattr(langsmith, "Client", forbidden)
    monkeypatch.setattr(observability, "_evaluate", lambda *args: {"answer": "local"})
    with observability.agent_task_tracing(False, None) as evaluate:
        assert evaluate(None, {}, "thread", 10) == {"answer": "local"}
