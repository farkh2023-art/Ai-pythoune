"""Illustrations libres de droits (Wikimedia Commons), synchronisées par scène.

L'épisode est découpé en *scènes* d'environ ``scene_seconds`` secondes, en
suivant le minutage exact des segments narrés. Pour chaque scène, on extrait
les mots les plus caractéristiques du texte et on cherche une image sur
Wikimedia Commons ; l'image reste affichée tant que la scène est lue.

Seules les images sous licence libre (domaine public, CC0, CC BY, CC BY-SA)
sont retenues, et leurs crédits (auteur, licence, lien) sont ajoutés à la
description YouTube, comme l'exigent les licences CC BY.

Les recherches et les images sont mises en cache : relancer une génération ne
retélécharge rien.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .splitter import count_words
from .tts import Timing

API_URL = "https://commons.wikimedia.org/w/api.php"
# Wikimedia demande un User-Agent identifiant l'outil et un moyen de contact.
USER_AGENT = "book-to-youtube/1.0 (https://github.com/farkh2023-art/Ai-pythoune)"

ALLOWED_LICENSES = ("public domain", "domaine public", "pd", "cc0", "cc by")
# Fichiers rarement utiles comme illustration de récit.
_BLOCKED_TITLE = re.compile(
    r"\b(map|carte|plan|logo|diagram|diagramme|chart|graph|flag|drapeau|coat of arms|"
    r"blason|signature|scan|page|texte|text|document|stamp|timbre|icon|ic[oô]ne)\b",
    re.IGNORECASE,
)

STOPWORDS = {
    "fr": set("""
        alors aussi autre autres avait avant avec avoir bien cela celle celles celui cependant
        ces cet cette ceux chaque chez comme comment dans depuis des deux donc dont elle elles
        encore entre était étaient être fait faire fois leur leurs lorsque mais même mêmes moins
        notre nous nôtre peut peu plus pour pourquoi quand quel quelle quelles quels quelque
        quelques sans selon sera seront sont sous suis tandis tant tout toute toutes tous très
        trop une vers votre vous voici voilà avais aurait aurais avions été ainsi après assez
        aucun aucune autant autour beaucoup ceci celui-ci chose choses contre déjà dès dessus
        dessous enfin ensuite jamais jusqu jusque lors moi-même monsieur madame non oui parce
        pendant personne peut-être plusieurs presque puis puisque quoi rien seul seule seulement
        soit surtout toujours toutefois trois vraiment lui eux mon ton son mes tes ses nos vos
        quelqu qu'il qu'elle c'était j'avais j'étais n'était n'avait dit dire voir vit vint
        sorte façon part côté moment temps instant heure heures jour jours nuit soir matin
        homme hommes femme gens fort grand grande petit petite vieux vieille autre premier
        première dernier dernière bon bonne mauvais long longue haut haute idée mot mots
    """.split()),
    "en": set("""
        about above after again against all also although always among another any anything
        around back because been before being below between both but could did does doing down
        during each even ever every from further had has have having here himself herself how
        however into its itself just like made make many more most much must myself never
        nothing now once only other others ought our ours ourselves over own perhaps quite
        rather said same should since some something still such than that their theirs them
        themselves then there these they thing things this those though through thus till
        together too under until upon very was were what when where whether which while who
        whom whose why will with within without would yet you your yours yourself time man men
        woman day days night long little great good old first last never upon came come went
    """.split()),
}
# Terminaisons verbales/adverbiales françaises fréquentes (mots peu « visuels »).
_FR_NON_NOUN = re.compile(r"(?:aient|ait|ais|èrent|erions|ions|iez|ment|ée|ées|és|é)$")
_EN_NON_NOUN = re.compile(r"(?:ed|ing|ly)$")
_WORD = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*")


@dataclass
class Illustration:
    path: Path
    title: str
    author: str
    license: str
    source_url: str
    personal: bool = False  # image fournie par le créateur (pas de crédit à afficher)

    def credit(self) -> str:
        return f"{self.title} — {self.author} — {self.license} — {self.source_url}"


@dataclass
class Scene:
    start: float
    end: float
    text: str
    illustration: Illustration | None = None
    queries: list[str] = field(default_factory=list)
    number: int = 0
    clip: Path | None = None  # animation fournie par le créateur (option « anime »)

    @property
    def duration(self) -> float:
        return self.end - self.start


# --- Scènes ----------------------------------------------------------------------
#
# Les scènes sont définies sur les segments du script *avant* la synthèse vocale,
# d'après une durée estimée (nombre de mots / débit). Elles sont donc connues dès
# l'import du livre : les prompts d'images et le dossier « scene_01.jpg… » du
# créateur correspondent exactement aux scènes de la vidéo finale. Seuls les
# segments de texte comptent : modifier le générique ne décale aucune scène.

def plan_scene_ranges(segments: list, config: dict) -> list[tuple[int, int]]:
    """(premier, dernier) indice de segment « body » de chaque scène."""
    wpm = config.get("words_per_minute", 150)
    target = float(config.get("scene_seconds", 40))
    pause = float(config.get("pause_between_segments", 0.25))
    body = [i for i, seg in enumerate(segments) if seg.kind == "body"]
    ranges: list[tuple[int, int]] = []
    first, elapsed = None, 0.0
    for i in body:
        if first is None:
            first, elapsed = i, 0.0
        elapsed += count_words(segments[i].text) / wpm * 60 + pause
        if elapsed >= target:
            ranges.append((first, i))
            first = None
    if first is not None:
        if ranges and elapsed < target / 3:
            ranges[-1] = (ranges[-1][0], body[-1])  # petite fin : rattachée à la précédente
        else:
            ranges.append((first, body[-1]))
    return ranges


def scene_texts(segments: list, ranges: list[tuple[int, int]]) -> list[str]:
    return [" ".join(seg.text for seg in segments[a:b + 1] if seg.kind == "body")
            for a, b in ranges]


def plan_scenes(timings: list[Timing], ranges: list[tuple[int, int]]) -> list[Scene]:
    """Scènes minutées : la 1re couvre aussi le générique, la dernière la conclusion."""
    if not timings:
        return []
    if not ranges:
        return [Scene(0.0, timings[-1].end, "", number=1)]
    texts = scene_texts(timings, ranges)
    scenes = []
    for k, (a, _b) in enumerate(ranges):
        start = 0.0 if k == 0 else timings[a].start
        end = timings[-1].end if k == len(ranges) - 1 else timings[ranges[k + 1][0]].start
        scenes.append(Scene(start, end, texts[k], number=k + 1))
    return scenes


# --- Mots-clés --------------------------------------------------------------------

def keywords(text: str, lang: str = "fr", document_freq: Counter | None = None,
             n_docs: int = 1, limit: int = 3) -> list[str]:
    """Mots les plus caractéristiques d'une scène (noms communs probables)."""
    counts = Counter(_candidates(text, lang))
    scores = {
        word: tf * math.log(1 + n_docs / (document_freq or {}).get(word, 1))
        for word, tf in counts.items()
    }
    return [w for w, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def _candidates(text: str, lang: str) -> list[str]:
    stop = STOPWORDS.get(lang, STOPWORDS["fr"])
    non_noun = _FR_NON_NOUN if lang == "fr" else _EN_NON_NOUN
    words = []
    for match in _WORD.finditer(text):
        word = match.group(0)
        start = match.start()
        # Majuscule en milieu de phrase : nom propre (personnage), peu utile à Commons.
        sentence_start = start == 0 or re.search(r"[.!?…»\"]\s*$", text[max(0, start - 3):start])
        if word[0].isupper() and not sentence_start:
            continue
        word = word.lower().replace("’", "'")
        word = word.split("'")[-1]  # « l'océan » -> « océan »
        if len(word) < 4 or word in stop or non_noun.search(word):
            continue
        words.append(word)
    return words


def build_queries(scenes: list[Scene], config: dict) -> None:
    """Calcule les requêtes de recherche de chaque scène (du plus précis au plus large)."""
    lang = config.get("language", "fr")
    hint = (config.get("illustration_hint") or "").strip()
    theme = (config.get("illustration_theme") or "").strip()
    df: Counter = Counter()
    for scene in scenes:
        df.update(set(_candidates(scene.text, lang)))
    for scene in scenes:
        words = keywords(scene.text, lang, df, len(scenes))
        queries = []
        if len(words) >= 2:
            queries.append(f"{words[0]} {words[1]}")
        queries.extend(words)
        if theme:
            queries.append(theme)
        scene.queries = list(dict.fromkeys(f"{q} {hint}".strip() for q in queries))


# --- Wikimedia Commons ------------------------------------------------------------

def _http_get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


class CommonsClient:
    """Recherche et téléchargement d'images sur Wikimedia Commons (avec cache disque)."""

    def __init__(self, cache_dir: str | Path, width: int = 1920, fetch=_http_get):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.width = width
        self.fetch = fetch
        self._index_path = self.cache_dir / "recherches.json"
        try:
            self._index = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._index = {}

    def search(self, query: str, limit: int = 20) -> list[dict]:
        if query in self._index:
            return self._index[query]
        params = {
            "action": "query", "format": "json", "formatversion": "2",
            "generator": "search", "gsrsearch": f"{query} filetype:bitmap",
            "gsrnamespace": "6", "gsrlimit": str(limit),
            "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": str(self.width),
            "iiextmetadatafilter": "LicenseShortName|Artist|ObjectName",
        }
        data = json.loads(self.fetch(f"{API_URL}?{urllib.parse.urlencode(params)}"))
        pages = sorted(data.get("query", {}).get("pages", []), key=lambda p: p.get("index", 0))
        results = [r for r in (_parse_page(p) for p in pages) if r]
        self._index[query] = results
        self._index_path.write_text(json.dumps(self._index, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
        return results

    def download(self, result: dict) -> Illustration:
        path = self.cache_dir / f"{result['pageid']}.jpg"
        if not path.exists():
            tmp = path.with_suffix(".part")
            tmp.write_bytes(self.fetch(result["image_url"]))
            tmp.replace(path)
        return Illustration(path=path, title=result["title"], author=result["author"],
                            license=result["license"], source_url=result["page_url"])


def _parse_page(page: dict) -> dict | None:
    info = (page.get("imageinfo") or [{}])[0]
    meta = info.get("extmetadata") or {}
    license_name = _plain(meta.get("LicenseShortName", {}).get("value", ""))
    title = re.sub(r"^(File|Fichier):", "", page.get("title", ""))
    width, height = info.get("width", 0), info.get("height", 1)
    if (info.get("mime") != "image/jpeg" or width < 1000 or width / max(height, 1) < 1.1
            or _BLOCKED_TITLE.search(title)
            or not any(lic in license_name.lower() for lic in ALLOWED_LICENSES)):
        return None
    return {
        "pageid": page["pageid"],
        "title": re.sub(r"\.jpe?g$", "", title, flags=re.I).replace("_", " "),
        "author": _plain(meta.get("Artist", {}).get("value", "")) or "auteur inconnu",
        "license": license_name,
        "image_url": info.get("thumburl") or info["url"],
        "page_url": info.get("descriptionurl", ""),
    }


def _plain(markup: str) -> str:
    text = re.sub(r"<[^>]+>", "", markup)
    return unicodedata.normalize("NFKC", re.sub(r"\s+", " ", text)).strip()


# --- Images personnelles -------------------------------------------------------------
#
# data/illustrations/<livre>/episode_001/scene_01.jpg (ou .png, .webp) : l'image
# déposée par le créateur pour la scène 1 de l'épisode 1. Elle a priorité sur
# Wikimedia Commons.

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
CLIP_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv")


def illustrations_root(config: dict) -> Path:
    return Path(config.get("illustrations_dir") or Path(config["data_dir"]) / "illustrations")


def episode_dir(config: dict, slug: str, number: int) -> Path:
    return illustrations_root(config) / slug / f"episode_{number:03d}"


def user_image(folder: Path, scene_number: int) -> Path | None:
    for ext in IMAGE_EXTENSIONS:
        path = folder / f"scene_{scene_number:02d}{ext}"
        if path.is_file():
            return path
    return None


def user_clip(folder: Path, scene_number: int) -> Path | None:
    """Animation de la scène (option « anime ») : scene_01.mp4, .mov, .webm…"""
    for ext in CLIP_EXTENSIONS:
        path = folder / f"scene_{scene_number:02d}{ext}"
        if path.is_file():
            return path
    return None


def attach_clips(scenes: list[Scene], user_dir: Path) -> int:
    """Option « anime » : chaque scène reçoit son clip s'il a été déposé."""
    for scene in scenes:
        scene.clip = user_clip(user_dir, scene.number)
    return sum(1 for s in scenes if s.clip)


# --- Attribution des images aux scènes ----------------------------------------------

def illustrate(scenes: list[Scene], config: dict, cache_dir: str | Path,
               client: CommonsClient | None = None, log=print,
               user_dir: Path | None = None) -> list[Scene]:
    """Image de chaque scène : la vôtre (dossier), sinon Wikimedia Commons, sinon la précédente."""
    source = config.get("illustration_source", "dossier+commons")
    use_commons = "commons" in source
    if user_dir is not None and "dossier" in source:
        for scene in scenes:
            path = user_image(user_dir, scene.number)
            if path is not None:
                scene.illustration = Illustration(path, f"Scène {scene.number}", "", "", "",
                                                  personal=True)
    if use_commons and any(s.illustration is None and not s.clip for s in scenes):
        client = client or CommonsClient(cache_dir,
                                         width=int(config.get("resolution", (1920,))[0]))
        build_queries(scenes, config)
    used: set[int] = set()
    previous: Illustration | None = None
    for scene in scenes:
        # Une scène animée (clip du créateur) n'a pas besoin d'image de Commons.
        if scene.illustration is None and use_commons and not scene.clip:
            for query in scene.queries:
                try:
                    results = client.search(query)
                except Exception as err:  # réseau indisponible, quota…
                    log(f"    illustration : recherche « {query} » impossible ({err})")
                    results = []
                choice = next((r for r in results if r["pageid"] not in used), None)
                if choice:
                    try:
                        scene.illustration = client.download(choice)
                    except Exception as err:
                        log(f"    illustration : téléchargement impossible ({err})")
                        continue
                    used.add(choice["pageid"])
                    break
        if scene.illustration is None:
            scene.illustration = previous
        previous = scene.illustration
    # Les premières scènes sans image prennent la première image trouvée.
    first = next((s.illustration for s in scenes if s.illustration), None)
    for scene in scenes:
        if scene.illustration is None:
            scene.illustration = first
    return scenes


def credits(scenes: list[Scene]) -> list[str]:
    """Crédits des images Wikimedia Commons (les vôtres n'en ont pas besoin)."""
    seen = dict.fromkeys(s.illustration.credit() for s in scenes
                         if s.illustration and not s.illustration.personal)
    return list(seen)


def to_json(scenes: list[Scene]) -> list[dict]:
    return [{**asdict(s), "illustration": (
        {**asdict(s.illustration), "path": str(s.illustration.path)} if s.illustration else None)}
        for s in scenes]
