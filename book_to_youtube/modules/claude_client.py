"""Appel commun à l'API Claude (résumés, prompts d'images).

Authentification : variable d'environnement ``ANTHROPIC_API_KEY`` (ou un
profil ``ant auth login``).
"""

from __future__ import annotations

import json
import os

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeError(RuntimeError):
    pass


def available() -> bool:
    """Une clé API est-elle configurée ? (sans clé, on n'appelle pas l'API)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def ask(system: str, prompt: str, config: dict, client=None, schema: dict | None = None,
        what: str = "cette demande") -> str:
    """Envoie une requête à Claude et renvoie le texte (JSON valide si `schema`)."""
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    output_config: dict = {"effort": config.get("summary_effort", "medium")}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    # Streaming : un chapitre entier peut être long, on évite les délais HTTP.
    with client.beta.messages.stream(
        model=config.get("summary_model", "claude-opus-5-5"),
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        thinking={"type": "adaptive"},
        output_config=output_config,
        # Si un filtre de sécurité refuse la requête, l'API la relance sur un
        # modèle de repli choisi automatiquement.
        betas=[FALLBACK_BETA],
        fallbacks="default",
    ) as stream:
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


def ask_json(system: str, prompt: str, config: dict, schema: dict, client=None,
             what: str = "cette demande") -> dict:
    text = ask(system, prompt, config, client, schema, what)
    try:
        return json.loads(text)
    except ValueError as err:
        raise ClaudeError(f"Réponse JSON invalide pour {what}.") from err
