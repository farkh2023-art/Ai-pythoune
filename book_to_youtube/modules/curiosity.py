"""Mode « Savez-vous pourquoi… ??? » : un mot-clé → 4 questions → Shorts et vidéo longue.

À partir d'un mot-clé (« sommeil ») et d'un domaine (« santé et corps humain »),
Claude écrit 4 questions, une par angle :

* **pourquoi**   — « Savez-vous pourquoi… ? »
* **saviez-vous** — « Saviez-vous que… ? » (un fait étonnant)
* **comment**    — « Savez-vous comment… ? »
* **quoi**       — « Savez-vous ce qu'est… ? »

Chaque question a son script de narration (~45 s : la question en accroche, la
réponse, un détail surprenant, une invitation à commenter), un titre YouTube, des
hashtags et les points à vérifier avant publication.

Le résultat est un fichier ``data/input/<mot-clé>.curiosite.json``, modifiable à la
main ou depuis le tableau de bord, puis traité comme un livre :

* une question = un épisode → 4 Shorts (``--format short``) ;
* les 4 questions enchaînées → une vidéo longue (``--split single``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import claude_client, languages

SUFFIX = ".curiosite.json"
ANGLES = ("pourquoi", "saviez-vous", "comment", "quoi")

_SCHEMA = {
    "type": "object",
    "properties": {
        "titre": {"type": "string"},
        "questions": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "angle": {"type": "string", "enum": list(ANGLES)},
                "question": {"type": "string"},
                "script": {"type": "string"},
                "titre_youtube": {"type": "string"},
                "hashtags": {"type": "array", "items": {"type": "string"}},
                "a_verifier": {"type": "string"},
            },
            "required": ["angle", "question", "script", "titre_youtube", "hashtags",
                         "a_verifier"],
            "additionalProperties": False,
        }},
    },
    "required": ["titre", "questions"],
    "additionalProperties": False,
}

_SYSTEM = ("Tu es l'auteur d'une chaîne YouTube de culture générale au format « Savez-vous "
           "pourquoi… ??? » : des questions qui piquent la curiosité, des réponses exactes, "
           "claires et captivantes, lues par une voix de synthèse.")

_PROMPT = """Mot-clé : « {keyword} »{domain}.
Langue de la chaîne : {langue}.{channel}

Écris 4 questions sur ce mot-clé, une par angle, dans cet ordre :
1. pourquoi — commence par l'équivalent de « Savez-vous pourquoi… ? »
2. saviez-vous — un fait étonnant : « Saviez-vous que… ? »
3. comment — « Savez-vous comment… ? »
4. quoi — « Savez-vous ce qu'est… ? » / « ce que… ? »

Pour chaque question :
- question : l'accroche, 15 mots au plus, qui donne envie d'avoir la réponse.
- script : le texte lu, environ {words} mots (≈ {seconds} secondes). Il commence par la
  question, répond clairement et précisément, ajoute un détail surprenant, et finit par une
  courte phrase qui invite le spectateur à commenter. Prose simple à lire à voix haute :
  pas de markdown, de liste, d'emoji ni de didascalie.
- titre_youtube : 70 caractères au plus, accrocheur mais honnête (pas de « clickbait »
  trompeur), avec un point d'interrogation.
- hashtags : 3 hashtags pertinents, sans le « # ».
- a_verifier : en une phrase, ce que le créateur doit vérifier avant de publier (chiffre,
  nom, date) et une source fiable où le faire (ex. Inserm, NASA, Météo-France, OMS).

Exactitude avant tout :
- Uniquement des faits établis et vérifiables ; pas de chiffre inventé, arrondis honnêtes.
- Une légende ou une anecdote incertaine est présentée comme telle (« selon la légende… »).
- Santé : informer sans donner de conseil médical.
- Les 4 questions sont différentes et ne se répètent pas.

titre : le titre de la série de 4 questions (60 caractères au plus), dans la langue de la
chaîne.

Tout le texte (titre, questions, scripts, titres YouTube, hashtags) est en {langue}, sauf
« a_verifier » qui est en français."""


def generate(keyword: str, config: dict, *, domain: str = "", channel_name: str = "",
             client=None) -> dict:
    """Les 4 questions du mot-clé, rédigées par Claude (dict prêt à enregistrer)."""
    lang = config.get("language", "fr")
    words = int(config.get("curiosity_words", 120))
    prompt = _PROMPT.format(
        keyword=keyword.strip(), domain=f" (domaine : {domain.strip()})" if domain.strip() else "",
        langue=languages.get(lang).name, words=words, seconds=round(words / 150 * 60),
        channel=f"\nNom de la chaîne : « {channel_name} »." if channel_name else "")
    data = claude_client.ask_json(_SYSTEM, prompt, config, _SCHEMA, client,
                                  what=f"les questions sur « {keyword} »")
    questions = [q for q in data.get("questions", []) if q.get("question") and q.get("script")]
    if len(questions) < 4:
        raise claude_client.ClaudeError(f"{len(questions)} question(s) reçue(s) au lieu de 4")
    return {"mot_cle": keyword.strip(), "domaine": domain.strip(), "langue": lang,
            "titre": data.get("titre") or keyword.strip(),
            "questions": [{**q, "hashtags": [h.lstrip("#").strip() for h in q["hashtags"]]}
                          for q in questions[:4]]}


def file_name(keyword: str, lang: str) -> str:
    from unicodedata import normalize
    base = normalize("NFKD", keyword).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-") or "question"
    return f"{base}-{lang}{SUFFIX}"


def save(data: dict, folder: str | Path) -> Path:
    """Nouveau fichier ; un fichier existant (déjà relu, déjà publié) n'est jamais écrasé :
    « sommeil-fr-2.curiosite.json », « -3 »…"""
    path = Path(folder) / file_name(data["mot_cle"], data.get("langue", "fr"))
    n = 2
    while path.exists():
        path = path.with_name(file_name(data["mot_cle"], data.get("langue", "fr"))
                              .replace(SUFFIX, f"-{n}{SUFFIX}"))
        n += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data.get("questions"), list) or not data["questions"]:
        raise ValueError(f"{Path(path).name} : aucune question.")
    return data


def is_curiosity(path: str | Path) -> bool:
    return str(path).lower().endswith(SUFFIX)


def to_book(data: dict) -> tuple[str, dict]:
    """Texte « livre » : une question = un chapitre (titre « Question n — … »)."""
    label = languages.get(data.get("langue")).labels["question"]
    parts = []
    for n, q in enumerate(data["questions"], 1):
        parts.append(f"{label} {n} — {q['question'].strip()}")
        parts.append(q["script"].strip())
    meta = {"title": data.get("titre") or data.get("mot_cle", ""), "curiosite": True,
            "langue": data.get("langue"),
            "hashtags": [q.get("hashtags", []) for q in data["questions"]],
            "youtube_titles": [q.get("titre_youtube", "") for q in data["questions"]]}
    return "\n\n".join(parts), meta


def question_of(title: str) -> str:
    """« Question 2 — Savez-vous… ? » → « Savez-vous… ? » (titre affiché d'un Short)."""
    labels = "|".join(sorted({re.escape(lang.labels["question"])
                              for lang in languages.LANGUAGES.values()}))
    return re.sub(rf"^(?:{labels})\s*\d+\s*[—:\-–]\s*", "", title).strip() or title
