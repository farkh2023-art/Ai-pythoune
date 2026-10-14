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


TEN_WORDS = "un deux trois quatre cinq six sept huit neuf dix."
SCENE_CFG = {"words_per_minute": 60, "pause_between_segments": 0, "scene_seconds": 30}


def test_scenes_are_planned_from_text_before_the_voice():
    # 9 segments de 10 mots = 10 s estimées chacun : 3 scènes de 3 segments.
    tl = timings([TEN_WORDS] * 9)
    ranges = illustrations.plan_scene_ranges(tl, SCENE_CFG)
    assert ranges == [(1, 3), (4, 6), (7, 9)]
    scenes = illustrations.plan_scenes(tl, ranges)
    # La 1re scène couvre le générique, la dernière la conclusion ; minutage réel.
    assert [(s.number, s.start, s.end) for s in scenes] == [
        (1, 0, 33.0), (2, 33.0, 63.0), (3, 63.0, 95.0)]
    assert "Intro" not in scenes[0].text and "Outro" not in scenes[-1].text


def test_short_tail_joins_previous_scene():
    tl = timings([TEN_WORDS] * 9)  # scènes de 40 s : il reste 10 s, moins d'un tiers
    assert illustrations.plan_scene_ranges(tl, {**SCENE_CFG, "scene_seconds": 40}) == [
        (1, 4), (5, 9)]


def test_changing_the_intro_does_not_shift_scenes():
    base = timings([TEN_WORDS] * 6)
    longer_intro = [Timing(0, 1, "Bienvenue.", "intro"), Timing(1, 2, "Présentation.", "intro")]
    shifted = longer_intro + base[1:]
    texts = lambda tl: illustrations.scene_texts(tl, illustrations.plan_scene_ranges(tl, SCENE_CFG))
    assert texts(base) == texts(shifted)


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


def _scenes(texts):
    tl = timings(texts, seconds=40)
    return illustrations.plan_scenes(tl, [(i, i) for i in range(1, len(texts) + 1)])


def test_each_scene_gets_a_distinct_image_with_fallbacks(tmp_path):
    fake = FakeCommons({
        "navire": [page(10, "Navire.jpg", index=1), page(11, "Navire 2.jpg", index=2)],
        "savane": [page(20, "Savane.jpg")],
    })
    scenes = _scenes(["Le navire entre au port. Le navire accoste.",
                      "Un autre navire, puis encore le navire.",
                      "Rien de précis ici, juste quelques mots vagues.",
                      "La savane immense, la savane brûlée."])
    illustrations.illustrate(scenes, {"language": "fr"}, tmp_path,
                             client=illustrations.CommonsClient(tmp_path, fetch=fake),
                             log=lambda *_: None)
    ids = [s.illustration.path.stem for s in scenes]
    assert ids[0] != ids[1]                 # pas deux fois la même image
    assert ids[2] == ids[1]                 # scène sans résultat : image précédente
    assert ids[3] == "20"
    assert len(illustrations.credits(scenes)) == 3


def test_personal_images_take_priority_and_have_no_credit(tmp_path):
    fake = FakeCommons({"navire": [page(10, "Navire.jpg")], "savane": [page(20, "Savane.jpg")]})
    mine = tmp_path / "mes_images"
    mine.mkdir()
    Image.new("RGB", (1600, 900)).save(mine / "scene_02.png")
    scenes = _scenes(["Le navire entre au port.", "La savane immense, la savane brûlée."])
    illustrations.illustrate(scenes, {"language": "fr"}, tmp_path / "cache",
                             client=illustrations.CommonsClient(tmp_path / "cache", fetch=fake),
                             log=lambda *_: None, user_dir=mine)
    assert scenes[0].illustration.path.stem == "10"
    assert scenes[1].illustration.path == mine / "scene_02.png"
    assert illustrations.credits(scenes) == [scenes[0].illustration.credit()]
    assert fake.queries and all("savane" not in q for q in fake.queries)  # rien cherché pour elle

    # Mode « dossier » seul : aucune recherche sur Commons.
    scenes = _scenes(["Le navire.", "La savane."])
    offline = illustrations.CommonsClient(tmp_path / "c2", fetch=lambda url: pytest.fail(url))
    illustrations.illustrate(scenes, {"illustration_source": "dossier"}, tmp_path / "c2",
                             client=offline, user_dir=mine, log=lambda *_: None)
    assert [s.illustration.path.name for s in scenes] == ["scene_02.png", "scene_02.png"]


def test_network_failure_leaves_scenes_without_image(tmp_path):
    def offline(url):
        raise OSError("réseau indisponible")
    scenes = _scenes(["Le navire."])
    illustrations.illustrate(scenes, {}, tmp_path,
                             client=illustrations.CommonsClient(tmp_path, fetch=offline),
                             log=lambda *_: None)
    assert all(s.illustration is None for s in scenes)
