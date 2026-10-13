"""Mode « résumé » : réécrit chaque épisode en narration résumée avec Claude.

Le résumé remplace le texte intégral de l'épisode avant la génération du
script ; le reste du pipeline (voix, visuels, montage) est inchangé.

Chaque résumé est enregistré dans ``data/episodes/<livre>/`` : le créateur
peut le relire et le corriger à la main, et une nouvelle génération réutilise
le fichier (aucun nouvel appel à l'API tant que le texte source et les
réglages ne changent pas).

Authentification : variable d'environnement ``ANTHROPIC_API_KEY`` (ou un
profil ``ant auth login``).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from pathlib import Path

from .splitter import Episode, count_words

FALLBACK_BETA = "server-side-fallback-2026-07-01"

PROMPTS = {
    "fr": {
        "system": (
            "Tu écris le texte de narration d'une série de vidéos YouTube qui résume un "
            "livre, épisode par épisode. Ton texte sera lu tel quel par une voix de synthèse."
        ),
        "instructions": """Résume l'extrait ci-dessous pour l'épisode {number} sur {total} de la série consacrée à « {book} »{author}.

Consignes :
- Environ {words} mots, en français.
- Prose continue, en paragraphes séparés par une ligne vide. Pas de titres, de listes, de markdown ni d'emoji : tout est lu à voix haute.
- Raconte au présent de narration, de façon vivante et claire pour un auditeur qui n'a pas lu le livre.
- Reste fidèle au texte : garde les personnages, les événements clés et les idées principales, dans l'ordre. N'invente rien et ne donne pas ton avis.
- Pas de formule d'accueil ni de conclusion du type « dans cet épisode » ou « à bientôt » : l'introduction et la conclusion de l'épisode sont ajoutées séparément.
- Réponds uniquement avec le texte de narration.

<extrait titre="{title}">
{text}
</extrait>""",
        "author": ", de {author}",
    },
    "en": {
        "system": (
            "You write the narration for a YouTube video series that summarizes a book, "
            "episode by episode. Your text will be read verbatim by a text-to-speech voice."
        ),
        "instructions": """Summarize the excerpt below for episode {number} of {total} of the series about "{book}"{author}.

Guidelines:
- About {words} words, in English.
- Continuous prose, in paragraphs separated by a blank line. No headings, lists, markdown or emoji: everything is read aloud.
- Narrate in the present tense, vividly and clearly, for a listener who has not read the book.
- Stay faithful to the text: keep the characters, key events and main ideas, in order. Do not invent anything or give your opinion.
- No greeting or closing such as "in this episode" or "see you soon": the episode intro and outro are added separately.
- Reply with the narration text only.

<excerpt title="{title}">
{text}
</excerpt>""",
        "author": ", by {author}",
    },
}


class SummaryError(RuntimeError):
    pass


def summarize_episode(episode: Episode, config: dict, *, book_title: str,
                      author: str | None = None, total_episodes: int = 1,
                      cache_dir: str | Path | None = None, client=None) -> Episode:
    """Retourne une copie de l'épisode dont le texte est le résumé narratif."""
    lang = config.get("language", "fr")
    prompts = PROMPTS.get(lang, PROMPTS["fr"])
    model = config.get("summary_model", "claude-opus-5-5")
    target = target_words(episode, config)
    prompt = prompts["instructions"].format(
        number=episode.number, total=total_episodes, book=book_title,
        author=prompts["author"].format(author=author) if author else "",
        words=target, title=episode.title, text=episode.text,
    )

    cache = None
    if cache_dir is not None:
        key = hashlib.sha1("\x1f".join([model, prompts["system"], prompt]).encode()).hexdigest()[:10]
        cache = Path(cache_dir) / f"episode_{episode.number:03d}.resume.{key}.txt"
        if cache.exists():
            return replace(episode, text=cache.read_text(encoding="utf-8").strip())

    summary = _call_claude(prompts["system"], prompt, config, client)
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(summary + "\n", encoding="utf-8")
    return replace(episode, text=summary)


def target_words(episode: Episode, config: dict) -> int:
    """Longueur visée : `summary_minutes` de narration, sans dépasser l'original."""
    wpm = config.get("words_per_minute", 150)
    wanted = int(config.get("summary_minutes", 5) * wpm)
    return max(50, min(wanted, episode.word_count))


def _call_claude(system: str, prompt: str, config: dict, client=None) -> str:
    import anthropic

    client = client or anthropic.Anthropic()
    # Streaming : un chapitre entier peut être long, on évite les délais HTTP.
    with client.beta.messages.stream(
        model=config.get("summary_model", "claude-opus-5-5"),
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        thinking={"type": "adaptive"},
        output_config={"effort": config.get("summary_effort", "medium")},
        # Si un filtre de sécurité refuse la requête, l'API la relance sur un
        # modèle de repli choisi automatiquement.
        betas=[FALLBACK_BETA],
        fallbacks="default",
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        raise SummaryError("Claude a refusé de résumer cet extrait : "
                           f"{getattr(response.stop_details, 'explanation', None) or 'raison non précisée'}")
    if response.stop_reason == "max_tokens":
        raise SummaryError("Résumé tronqué (limite de tokens atteinte).")
    text = "\n\n".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise SummaryError("Claude n'a renvoyé aucun texte.")
    return _clean(text)


def _clean(text: str) -> str:
    """Retire le balisage éventuel et normalise les paragraphes."""
    text = re.sub(r"^[ \t]*#+[ \t]*", "", text, flags=re.M)        # titres markdown
    text = re.sub(r"^[ \t]*(?:[-*•]|\d+\.)[ \t]+", "", text, flags=re.M)  # puces
    text = re.sub(r"\*\*|__|\*", "", text)
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    return "\n\n".join(p for p in paragraphs if p)


def summary_ratio(original: Episode, summarized: Episode) -> float:
    return count_words(summarized.text) / max(original.word_count, 1)
