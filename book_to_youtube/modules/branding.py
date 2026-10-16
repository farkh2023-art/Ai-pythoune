"""Identité de la chaîne et présentation des livres (générique de chaque épisode).

* ``data/chaine/chaine.json`` : ``{"nom": "...", "nom_lu": "...", "slogan": "..."}``
  (``nom_lu`` : le nom tel qu'il doit être prononcé par la voix, par exemple
  « Le Grenier du Savoir » pour « GrenieR_du_SavoiR ») ;
* ``data/chaine/logo.png`` (ou .jpg / .webp) : logo affiché sur le générique et
  dans le coin des images ;
* ``data/episodes/<livre>/presentation.txt`` : introduction / description du
  livre, lue et affichée au début de chaque épisode.

Tout est facultatif : sans ces fichiers, le générique se limite au titre du
livre et de l'épisode.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

LOGO_NAMES = ("logo.png", "logo.jpg", "logo.jpeg", "logo.webp")


@dataclass
class Channel:
    name: str = ""
    slogan: str = ""
    logo: Path | None = None
    spoken_name: str = ""

    @property
    def narrated_name(self) -> str:
        """Nom lu par la voix : `nom_lu`, sinon le nom sans tirets bas."""
        return self.spoken_name or self.name.replace("_", " ").strip()


def channel_dir(config: dict) -> Path:
    return Path(config.get("channel_dir") or Path(config["data_dir"]) / "chaine")


def load_channel(config: dict) -> Channel:
    folder = channel_dir(config)
    try:
        data = json.loads((folder / "chaine.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    logo = next((folder / n for n in LOGO_NAMES if (folder / n).is_file()), None)
    return Channel(name=str(data.get("nom", "")).strip(),
                   slogan=str(data.get("slogan", "")).strip(), logo=logo,
                   spoken_name=str(data.get("nom_lu", "")).strip())


def save_channel(config: dict, name: str, slogan: str, spoken_name: str = "") -> Channel:
    folder = channel_dir(config)
    folder.mkdir(parents=True, exist_ok=True)
    data = {"nom": name.strip(), "nom_lu": spoken_name.strip(), "slogan": slogan.strip()}
    (folder / "chaine.json").write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    return load_channel(config)


def save_logo(config: dict, data: bytes, suffix: str) -> Path:
    """Remplace le logo (un seul fichier logo.* à la fois)."""
    suffix = suffix.lower()
    if f"logo{suffix}" not in LOGO_NAMES:
        raise ValueError("Logo : formats acceptés PNG, JPG ou WEBP.")
    folder = channel_dir(config)
    folder.mkdir(parents=True, exist_ok=True)
    for name in LOGO_NAMES:
        (folder / name).unlink(missing_ok=True)
    path = folder / f"logo{suffix}"
    path.write_bytes(data)
    return path


def presentation_path(config: dict, slug: str) -> Path:
    return Path(config["episodes_dir"]) / slug / "presentation.txt"


def load_presentation(config: dict, slug: str) -> str:
    try:
        return presentation_path(config, slug).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_presentation(config: dict, slug: str, text: str) -> None:
    path = presentation_path(config, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


# --- Présentation rédigée par Claude -------------------------------------------------

PRESENTATION_PROMPTS = {
    "fr": (
        "Tu écris la présentation d'un livre, lue par une voix de synthèse au début de chaque "
        "épisode d'une série YouTube qui le raconte.",
        "Écris la présentation du livre « {book} »{author} pour la chaîne « {channel} ».\n\n"
        "Consignes :\n"
        "- 3 à 5 phrases, 60 à 90 mots, en {langue}, au présent.\n"
        "- Ton chaleureux et captivant : l'époque, le lieu, le héros et l'enjeu, pour donner "
        "envie d'écouter la suite.\n"
        "- Ne révèle ni la fin ni les grands rebondissements.\n"
        "- Pas de formule d'accueil et ne commence pas par le titre ni l'auteur : ils sont "
        "déjà annoncés juste avant.\n"
        "- Prose simple à lire à voix haute : pas de markdown, de liste, ni de guillemets "
        "autour du texte. N'invente rien qui ne soit dans le livre.\n"
        "- Réponds uniquement avec la présentation.\n\n<livre>\n{text}\n</livre>",
    ),
    "en": (
        "You write the presentation of a book, read by a text-to-speech voice at the start of "
        "every episode of a YouTube series that narrates it.",
        "Write the presentation of the book \"{book}\"{author} for the channel \"{channel}\".\n\n"
        "Guidelines:\n"
        "- 3 to 5 sentences, 60 to 90 words, in English, present tense.\n"
        "- Warm and gripping: the era, the place, the hero and the stakes.\n"
        "- Do not reveal the ending or major twists.\n"
        "- No greeting, and do not start with the title or author: they are announced just "
        "before.\n"
        "- Plain prose for reading aloud: no markdown, lists or quotes around the text. "
        "Do not invent anything that is not in the book.\n"
        "- Reply with the presentation only.\n\n<book>\n{text}\n</book>",
    ),
}


def generate_presentation(config: dict, *, book_title: str, author: str | None, text: str,
                          channel_name: str = "", client=None) -> str:
    """Fait rédiger la présentation du livre par Claude (clé API requise)."""
    import re

    from . import claude_client, languages

    lang = config.get("language", "fr")
    system, template = PRESENTATION_PROMPTS["en" if lang == "en" else "fr"]
    by = (" by " if lang == "en" else " de ") + author if author else ""
    prompt = template.format(book=book_title, author=f",{by}" if author else "",
                             channel=channel_name or ("the channel" if lang == "en" else "la chaîne"),
                             langue=languages.get(lang).name,
                             text=text[:400_000])
    answer = claude_client.ask(system, prompt, config, client, what="la présentation du livre")
    answer = re.sub(r"[*_#`]", "", answer)
    answer = re.sub(r"\s+", " ", answer).strip().strip("«»\"“”").strip()
    return answer


def ensure_presentation(config: dict, slug: str, *, book_title: str, author: str | None,
                        text: str, channel_name: str = "", client=None, log=print) -> str:
    """Présentation existante, sinon rédigée par Claude si possible (puis enregistrée)."""
    from . import claude_client

    existing = load_presentation(config, slug)
    if existing or not config.get("auto_presentation", True) or (
            client is None and not claude_client.available()):
        return existing
    try:
        text_ = generate_presentation(config, book_title=book_title, author=author, text=text,
                                      channel_name=channel_name, client=client)
    except Exception as err:
        log(f"    présentation : rédaction par Claude impossible ({err})")
        return ""
    save_presentation(config, slug, text_)
    log(f"Présentation rédigée par Claude (modifiable) : {presentation_path(config, slug)}")
    return text_
