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
