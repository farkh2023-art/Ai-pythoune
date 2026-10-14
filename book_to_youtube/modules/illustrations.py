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

    def credit(self) -> str:
        return f"{self.title} — {self.author} — {self.license} — {self.source_url}"


@dataclass
class Scene:
    start: float
    end: float
    text: str
    illustration: Illustration | None = None
    queries: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


# --- Scènes ----------------------------------------------------------------------

def plan_scenes(timings: list[Timing], scene_seconds: float = 40) -> list[Scene]:
    """Regroupe les segments en scènes ; une scène change toujours entre deux segments."""
    scenes: list[Scene] = []
    current: list[Timing] = []
    for timing in timings:
        current.append(timing)
        if timing.kind == "body" and current[-1].end - current[0].start >= scene_seconds:
            scenes.append(_scene(current))
            current = []
    if current:
        if scenes and current[-1].end - current[0].start < scene_seconds / 3:
            # Petite fin (outro…) : on la rattache à la scène précédente.
            last = scenes.pop()
            scenes.append(Scene(last.start, current[-1].end,
                                f"{last.text} {_scene(current).text}".strip()))
        else:
            scenes.append(_scene(current))
    return scenes


def _scene(timings: list[Timing]) -> Scene:
    text = " ".join(t.text for t in timings if t.kind == "body")
    return Scene(timings[0].start, timings[-1].end, text)


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


# --- Attribution des images aux scènes ----------------------------------------------

def illustrate(scenes: list[Scene], config: dict, cache_dir: str | Path,
               client: CommonsClient | None = None, log=print) -> list[Scene]:
    """Choisit une image distincte par scène ; à défaut, réutilise la précédente."""
    client = client or CommonsClient(cache_dir, width=int(config.get("resolution", (1920,))[0]))
    build_queries(scenes, config)
    used: set[int] = set()
    previous: Illustration | None = None
    for scene in scenes:
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
    seen = dict.fromkeys(s.illustration.credit() for s in scenes if s.illustration)
    return list(seen)


def to_json(scenes: list[Scene]) -> list[dict]:
    return [{**asdict(s), "illustration": (
        {**asdict(s.illustration), "path": str(s.illustration.path)} if s.illustration else None)}
        for s in scenes]
