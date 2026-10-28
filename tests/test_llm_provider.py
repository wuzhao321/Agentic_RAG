import pytest

from kb.config import load_config
from kb.runtime import llm_identity, llm_settings


def test_deepseek_uses_own_key_endpoint_and_non_thinking_mode():
    provider, key, params = llm_settings(
        load_config("agent"),
        {
            "DEEPSEEK_API_KEY": "test-deepseek",
            "DEEPSEEK_MODEL": "deepseek-flash",
            "DASHSCOPE_API_KEY": "must-not-use",
        },
    )
    assert provider == "deepseek" and key == "test-deepseek"
    assert params["base_url"] == "https://api.deepseek.com"
    assert params["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "seed" not in params


def test_provider_does_not_fallback_to_another_providers_key():
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        llm_settings(
            load_config("agent"),
            {"DASHSCOPE_API_KEY": "wrong-provider", "DEEPSEEK_MODEL": "deepseek-flash"},
        )
    with pytest.raises(ValueError, match="未配置模型供应商"):
        llm_settings(load_config("agent"), {"LLM_PROVIDER": "unknown"})


def test_dashscope_remains_explicit_and_identity_excludes_keys():
    env = {
        "LLM_PROVIDER": "dashscope",
        "DASHSCOPE_API_KEY": "secret-test-only",
        "QWEN_MODEL": "qwen-test-version",
    }
    provider, key, params = llm_settings(load_config("agent"), env)
    assert provider == "dashscope" and key == "secret-test-only"
    assert params["base_url"].startswith("https://dashscope.aliyuncs.com/")
    assert params["seed"] == 42 and "extra_body" not in params
    identity = llm_identity(load_config("agent"), env)
    assert "secret-test-only" not in str(identity)
    assert identity["parameters"]["model"] == "qwen-test-version"
