"""Appel commun à l'API Claude (résumés, prompts d'images, OCR).

Authentification : variable d'environnement ``ANTHROPIC_API_KEY`` (ou un
profil ``ant auth login``).

Le SDK ``anthropic`` est utilisé s'il est installé. Sinon (Android/Termux, où
sa dépendance pydantic-core doit être compilée), l'API est appelée directement
en HTTP avec la bibliothèque standard : mêmes paramètres, même résultat.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from types import SimpleNamespace

FALLBACK_BETA = "server-side-fallback-2026-07-01"
API_BASE = "https://api.anthropic.com"


class ClaudeError(RuntimeError):
    pass


def available() -> bool:
    """Une clé API est-elle configurée ? (sans clé, on n'appelle pas l'API)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def ask(system: str, prompt: str | list, config: dict, client=None, schema: dict | None = None,
        what: str = "cette demande", max_tokens: int = 16000) -> str:
    """Envoie une requête à Claude et renvoie le texte (JSON valide si `schema`).

    `prompt` : texte, ou liste de blocs de contenu (images + texte pour l'OCR).
    """
    output_config: dict = {"effort": config.get("summary_effort", "medium")}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    params = dict(
        model=config.get("summary_model", "claude-opus-5-5"),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        thinking={"type": "adaptive"},
        output_config=output_config,
        # Si un filtre de sécurité refuse la requête, l'API la relance sur un
        # modèle de repli choisi automatiquement.
        fallbacks="default",
    )
    if client is None:
        try:
            import anthropic
        except ImportError:
            response = _http_request(params)
        else:
            client = anthropic.Anthropic()
    if client is not None:
        # Streaming : un chapitre entier peut être long, on évite les délais HTTP.
        with client.beta.messages.stream(betas=[FALLBACK_BETA], **params) as stream:
            response = stream.get_final_message()

    if response.stop_reason == "refusal":
        reason = getattr(response.stop_details, "explanation", None) or "raison non précisée"
        raise ClaudeError(f"Claude a refusé {what} : {reason}")
    if response.stop_reason == "max_tokens":
        raise ClaudeError(f"Réponse tronquée pour {what} (limite de tokens atteinte).")
    text = "\n\n".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise ClaudeError(f"Claude n'a renvoyé aucun texte pour {what}.")
    return text


def ask_json(system: str, prompt: str | list, config: dict, schema: dict, client=None,
             what: str = "cette demande", max_tokens: int = 16000) -> dict:
    text = ask(system, prompt, config, client, schema, what, max_tokens)
    try:
        return json.loads(text)
    except ValueError as err:
        raise ClaudeError(f"Réponse JSON invalide pour {what}.") from err


def _http_request(params: dict) -> SimpleNamespace:
    """Appel direct de l'API (sans le SDK), réponse au même format que le SDK."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not key and not token:
        raise ClaudeError("Clé Claude absente : export ANTHROPIC_API_KEY=…")
    headers = {"content-type": "application/json", "anthropic-version": "2023-06-01",
               "anthropic-beta": FALLBACK_BETA}
    if key:
        headers["x-api-key"] = key
    else:
        headers["authorization"] = f"Bearer {token}"
    base = os.environ.get("ANTHROPIC_BASE_URL") or API_BASE
    request = urllib.request.Request(base.rstrip("/") + "/v1/messages",
                                     data=json.dumps(params).encode(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=900) as reply:
            data = json.loads(reply.read())
    except urllib.error.HTTPError as err:
        try:
            message = json.loads(err.read())["error"]["message"]
        except Exception:
            message = err.reason
        raise ClaudeError(f"API Claude (HTTP {err.code}) : {message}") from None
    except urllib.error.URLError as err:
        raise ClaudeError(f"API Claude injoignable : {err.reason}") from None
    details = data.get("stop_details") or {}
    return SimpleNamespace(
        stop_reason=data.get("stop_reason"),
        stop_details=SimpleNamespace(explanation=details.get("explanation")),
        content=[SimpleNamespace(type=b.get("type"), text=b.get("text", ""))
                 for b in data.get("content", [])],
    )
