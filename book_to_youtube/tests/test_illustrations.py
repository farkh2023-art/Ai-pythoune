import io
import json
import urllib.parse

import pytest
from PIL import Image

from modules import illustrations
from modules.tts import Timing


def jpeg_bytes(color=(200, 120, 40)):
    buf = io.BytesIO()
    Image.new("RGB", (1600, 900), color).save(buf, "JPEG")
    return buf.getvalue()


def page(pageid, title, *, mime="image/jpeg", width=2000, height=1300, license="CC BY-SA 4.0",
         artist='<a href="//commons.wikimedia.org/wiki/User:X">Jean Photo</a>', index=1):
    return {"pageid": pageid, "title": f"File:{title}", "index": index, "imageinfo": [{
        "mime": mime, "width": width, "height": height,
        "url": f"https://upload.example/{pageid}.jpg",
        "thumburl": f"https://upload.example/thumb/{pageid}.jpg",
        "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{title}",
        "extmetadata": {"LicenseShortName": {"value": license}, "Artist": {"value": artist}},
    }]}


class FakeCommons:
    """Imite l'API Wikimedia Commons : `pages_by_term` associe un mot à des résultats."""

    def __init__(self, pages_by_term):
        self.pages_by_term = pages_by_term
        self.queries = []

    def __call__(self, url):
        if "upload.example" in url:
            return jpeg_bytes()
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["gsrsearch"][0]
        query = query.replace(" filetype:bitmap", "")
        self.queries.append(query)
        pages = [p for term, ps in self.pages_by_term.items() if term in query.split() for p in ps]
        return json.dumps({"query": {"pages": pages}} if pages else {}).encode()


def timings(texts, seconds=10.0):
    out, t = [Timing(0, 3, "Intro.", "intro")], 3.0
    for text in texts:
        out.append(Timing(t, t + seconds, text, "body"))
        t += seconds
    out.append(Timing(t, t + 2, "Outro.", "outro"))
    return out


def test_scenes_follow_segment_boundaries():
    scenes = illustrations.plan_scenes(timings(["a."] * 9), scene_seconds=30)
    assert [(s.start, s.end) for s in scenes] == [(0, 33.0), (33.0, 63.0), (63.0, 95.0)]
    # L'outro (2 s) est rattachée à la dernière scène au lieu d'en créer une.


def test_keywords_prefer_distinctive_nouns():
    text = ("Le navire quitta le port. Laputa regardait le navire et la mer grise. "
            "Il marchait longtemps vers les rochers, puis le navire disparut.")
    words = illustrations.keywords(text, "fr")
    assert words[0] == "navire"
    assert "laputa" not in words and "marchait" not in words and "longtemps" not in words


def test_commons_filtering_and_credits(tmp_path):
    fake = FakeCommons({"navire": [
        page(1, "Carte du port.jpg", index=1),                  # titre bloqué (carte)
        page(2, "Navire.png", mime="image/png", index=2),       # pas un JPEG
        page(3, "Navire portrait.jpg", width=900, index=3),     # trop petit
        page(4, "Navire non libre.jpg", license="", index=4),   # licence inconnue
        page(5, "Navire_au_port.jpg", index=5),                 # retenu
    ]})
    client = illustrations.CommonsClient(tmp_path, fetch=fake)
    (result,) = client.search("navire")
    assert result["pageid"] == 5 and result["author"] == "Jean Photo"
    illus = client.download(result)
    assert illus.path.exists() and illus.title == "Navire au port"
    assert illus.credit().startswith("Navire au port — Jean Photo — CC BY-SA 4.0 — https://")

    # Deuxième client : tout vient du cache, aucun appel réseau.
    offline = illustrations.CommonsClient(tmp_path, fetch=lambda url: pytest.fail(url))
    assert offline.search("navire")[0]["pageid"] == 5


def test_each_scene_gets_a_distinct_image_with_fallbacks(tmp_path):
    fake = FakeCommons({
        "navire": [page(10, "Navire.jpg", index=1), page(11, "Navire 2.jpg", index=2)],
        "savane": [page(20, "Savane.jpg")],
    })
    texts = ["Le navire entre au port. Le navire accoste.",
             "Un autre navire, puis encore le navire.",
             "Rien de précis ici, juste quelques mots vagues.",
             "La savane immense, la savane brûlée."]
    scenes = illustrations.plan_scenes(timings(texts, seconds=40), scene_seconds=40)
    illustrations.illustrate(scenes, {"language": "fr"}, tmp_path,
                             client=illustrations.CommonsClient(tmp_path, fetch=fake),
                             log=lambda *_: None)
    ids = [s.illustration.path.stem for s in scenes]
    assert ids[0] != ids[1]                 # pas deux fois la même image
    assert ids[2] == ids[1]                 # scène sans résultat : image précédente
    assert ids[3] == "20"
    assert len(illustrations.credits(scenes)) == 3


def test_network_failure_leaves_scenes_without_image(tmp_path):
    def offline(url):
        raise OSError("réseau indisponible")
    scenes = illustrations.plan_scenes(timings(["Le navire."]), scene_seconds=40)
    illustrations.illustrate(scenes, {}, tmp_path,
                             client=illustrations.CommonsClient(tmp_path, fetch=offline),
                             log=lambda *_: None)
    assert all(s.illustration is None for s in scenes)
