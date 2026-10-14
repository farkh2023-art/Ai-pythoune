import io
import json
import sys
import urllib.error

import pytest

from modules import claude_client


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def no_sdk(monkeypatch):
    """Comme sous Termux : le SDK anthropic n'est pas installé."""
    monkeypatch.setitem(sys.modules, "anthropic", None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


def test_http_fallback_without_sdk(no_sdk, monkeypatch):
    sent = {}

    def urlopen(request, timeout):
        sent["url"], sent["headers"] = request.full_url, dict(request.headers)
        sent["body"] = json.loads(request.data)
        return Reply(json.dumps({"stop_reason": "end_turn", "content": [
            {"type": "thinking", "thinking": "…"}, {"type": "text", "text": "Bonjour."}]}).encode())

    monkeypatch.setattr(claude_client.urllib.request, "urlopen", urlopen)
    schema = {"type": "object"}
    assert claude_client.ask("système", "question", {"summary_effort": "low"},
                             schema=schema) == "Bonjour."
    assert sent["url"] == "https://api.anthropic.com/v1/messages"
    assert sent["headers"]["X-api-key"] == "sk-test"
    assert sent["headers"]["Anthropic-beta"] == claude_client.FALLBACK_BETA
    body = sent["body"]
    assert body["system"] == "système" and body["messages"][0]["content"] == "question"
    assert body["output_config"] == {"effort": "low",
                                     "format": {"type": "json_schema", "schema": schema}}
    assert body["thinking"] == {"type": "adaptive"} and body["fallbacks"] == "default"


def test_http_fallback_errors_are_readable(no_sdk, monkeypatch):
    def urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {},
                                     io.BytesIO(b'{"error": {"message": "invalid x-api-key"}}'))

    monkeypatch.setattr(claude_client.urllib.request, "urlopen", urlopen)
    with pytest.raises(claude_client.ClaudeError, match="HTTP 401.*invalid x-api-key"):
        claude_client.ask("s", "q", {})


def test_http_fallback_refusal(no_sdk, monkeypatch):
    monkeypatch.setattr(claude_client.urllib.request, "urlopen", lambda r, timeout: Reply(
        json.dumps({"stop_reason": "refusal", "stop_details": {"explanation": "non"},
                    "content": []}).encode()))
    with pytest.raises(claude_client.ClaudeError, match="refusé ce résumé : non"):
        claude_client.ask("s", "q", {}, what="ce résumé")
