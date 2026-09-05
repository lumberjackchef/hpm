"""Tests for the multi-provider LLM client."""

from unittest.mock import patch

import pytest

from hpm import llm


class TestProviderResolution:
    def test_default_provider_is_zai(self):
        """The module default (unset env) resolves to zai — reads the constant,
        not the import-time-resolved value, so it is hermetic to machine config."""
        from hpm import config as hpm_config

        assert hpm_config.DEFAULT_LLM_PROVIDER == "zai"

    def test_unknown_provider_raises(self, monkeypatch):
        monkeypatch.setattr("hpm.config.LLM_PROVIDER", "nonexistent")
        with pytest.raises(ValueError, match="Unknown"):
            llm._provider_config()
        with pytest.raises(
            ValueError, match="zai, opencode, openai, openrouter, anthropic"
        ):
            llm._provider_config()

    def test_sidecar_provider_keys_include_zai(self, monkeypatch):
        """Sidecar's provider key map must know zai (GLM key)."""
        from hpm import config as hpm_config

        monkeypatch.setattr("hpm.config.GLM_API_KEY", "sk-sidecar-test")
        provider_keys = {
            "zai": hpm_config.GLM_API_KEY,
            "opencode": hpm_config.OPENGINE_API_KEY,
            "anthropic": hpm_config.ANTHROPIC_API_KEY,
            "openai": hpm_config.OPENAI_API_KEY,
            "openrouter": hpm_config.OPENROUTER_API_KEY,
        }
        assert provider_keys["zai"] == "sk-sidecar-test"
        # unknown providers must not silently resolve to a mapped key
        assert provider_keys.get("nonexistent") is None


class TestOpenAICall:
    def test_success(self, monkeypatch):
        """OpenAI-compatible call returns the message content."""
        monkeypatch.setattr("hpm.config.LLM_PROVIDER", "zai")
        monkeypatch.setattr("hpm.config.GLM_API_KEY", "sk-test")
        mock_response = {
            "choices": [{"message": {"content": "Hello from AI"}}]
        }

        with patch("hpm.llm.httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = mock_response

            result = llm.complete(
                messages=[{"role": "user", "content": "hi"}],
                system="Be helpful",
            )

        assert result == "Hello from AI"

        # Verify the request format
        call_kwargs = mock_post.call_args[1]
        body = call_kwargs["json"]
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] == "Be helpful"
        assert body["messages"][1]["role"] == "user"

    def test_missing_api_key(self, monkeypatch):
        """Raises ValueError when API key is empty."""
        monkeypatch.setattr("hpm.config.LLM_PROVIDER", "zai")
        monkeypatch.setattr("hpm.config.GLM_API_KEY", "")
        with pytest.raises(ValueError, match="API key"):
            llm.complete(messages=[{"role": "user", "content": "hi"}])


class TestAnthropicCall:
    def test_success(self, monkeypatch):
        """Anthropic call returns collected text blocks."""
        monkeypatch.setattr("hpm.config.LLM_PROVIDER", "anthropic")
        monkeypatch.setattr("hpm.config.ANTHROPIC_API_KEY", "sk-ant-test")

        mock_response = {
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "text", "text": " from Claude"},
            ]
        }

        with patch("hpm.llm.httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = mock_response

            result = llm.complete(
                messages=[{"role": "user", "content": "hi"}],
                system="Be concise",
            )

        assert result == "Hello\n from Claude"

        # Verify Anthropic request format
        call_kwargs = mock_post.call_args[1]
        body = call_kwargs["json"]
        assert "system" in body
        assert body["system"] == "Be concise"
        assert body["messages"] == [{"role": "user", "content": "hi"}]
        assert call_kwargs["headers"]["x-api-key"] == "sk-ant-test"

    def test_missing_api_key(self, monkeypatch):
        """Raises ValueError when Anthropic key is empty."""
        monkeypatch.setattr("hpm.config.LLM_PROVIDER", "anthropic")
        monkeypatch.setattr("hpm.config.ANTHROPIC_API_KEY", "")
        with pytest.raises(ValueError, match="API key"):
            llm.complete(messages=[{"role": "user", "content": "hi"}])
