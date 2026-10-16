import json
import shutil

import pytest

import main
from modules import claude_client, languages, scripter, splitter, titles, translator, tts, visuals
from modules.splitter import Episode

FR = ("Le vieil homme monta au sommet de la montagne. Il regarda la vallée et les nuages "
      "qui passaient sur les crêtes. Dans le village, les enfants jouaient pour la journée.")
EN = "The old man climbed to the top of the mountain and looked at the valley with his son."
AR = "صعد الشيخ إلى قمة الجبل. نظر إلى الوادي والسحب التي تمر فوق القمم."


def test_detect_book_language():
    assert languages.detect(FR) == "fr"
    assert languages.detect(EN) == "en"
    assert languages.detect(AR) == "ar"
    assert languages.detect("El viejo subió a la montaña con los niños para ver el valle "
                            "pero también la casa") == "es"


def test_every_language_is_complete():
    keys = set(languages.get("fr").labels), set(languages.get("fr").script)
    for lang in languages.LANGUAGES.values():
        assert (set(lang.labels), set(lang.script)) == keys, lang.code
        assert lang.voices and all(v.startswith(lang.code + "-") for v, _ in lang.voices)
    assert languages.get("xx").code == "fr"


def test_voice_follows_the_language():
    assert tts.voice_for("edge", "ar", None) == "ar-SA-HamedNeural"
    assert tts.voice_for("edge", "ar", "ar-MA-JamalNeural") == "ar-MA-JamalNeural"
    assert tts.voice_for("edge", "ar", "fr-FR-HenriNeural") == "ar-SA-HamedNeural"
    assert tts.voice_for("edge", "fr", None) == "fr-FR-DeniseNeural"
    assert tts.voice_for("gtts", "es", None) == "es"


def test_arabic_text_is_split_into_sentences_and_chapters():
    assert splitter.split_sentences(AR) == ["صعد الشيخ إلى قمة الجبل.",
                                            "نظر إلى الوادي والسحب التي تمر فوق القمم."]
    text = f"الفصل الأول — القمة\n\n{AR}\n\nالفصل الثاني — الوادي\n\n{AR}"
    eps = splitter.split(text, {"split_mode": "chapter", "min_words": 0})
    assert [e.title for e in eps] == ["الفصل الأول — القمة", "الفصل الثاني — الوادي"]
    default = splitter.split(AR, {"split_mode": "duration", "language": "ar"})[0].title
    assert default == "الجزء 1" and titles.needs_title(Episode(1, default, AR))


def test_arabic_script_and_labels():
    ep = Episode(2, "القمة", AR)
    script = scripter.make_script(ep, {"language": "ar"}, book_title="الرجل والجبل",
                                  author="عتيق", total_episodes=3, channel_name="مخزن المعرفة")
    texts = [s.text for s in script.segments]
    assert texts[0].startswith("مرحبًا بكم في مخزن المعرفة.") and "الحلقة 2: القمة." in texts[0]
    assert texts[-1].startswith("انتهت الحلقة 2.")


def test_visual_order_without_raqm():
    # Sans raqm : lettres liées puis ligne inversée ; le latin et les nombres restent lisibles.
    out = visuals.visual_order("الحلقة 12 مع Jarvis")
    assert "Jarvis" in out and "12" in out
    assert out.index("Jarvis") < out.index("12")


@pytest.mark.parametrize("raqm", [True, False])
def test_arabic_cards_render(monkeypatch, raqm):
    monkeypatch.setattr(visuals, "_has_raqm", lambda: raqm)
    config = {"resolution": (640, 360), "language": "ar"}
    r = visuals.SlideRenderer(config, "الرجل والجبل", 1, "القمة", presentation=AR)
    assert r.intro_card().size == (640, 360)
    assert r.card_slide(AR, 0.5).size == (640, 360)
    visuals.set_language("fr")


def fake_claude(monkeypatch, calls):
    def ask(system, prompt, config, client=None, schema=None, what="", max_tokens=16000):
        calls.append(what)
        if "traduction de" in what:
            return AR
        raise claude_client.ClaudeError("non simulé")

    def ask_json(system, prompt, config, schema, client=None, what="", max_tokens=16000):
        calls.append(what)
        if "traduction des titres" in what:
            n = int(prompt.split(" ces ")[1].split(" ")[0])
            return {"titles": [f"عنوان {i}" for i in range(1, n + 1)]}
        raise claude_client.ClaudeError("non simulé")

    monkeypatch.setattr(claude_client, "ask", ask)
    monkeypatch.setattr(claude_client, "ask_json", ask_json)
    monkeypatch.setattr(claude_client, "available", lambda: True)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg requis")
def test_french_book_becomes_an_arabic_series(tmp_path, monkeypatch):
    calls = []
    fake_claude(monkeypatch, calls)
    book = tmp_path / "montagne.txt"
    book.write_text(f"Chapitre 1 — Le sommet\n\n{FR}\n\nChapitre 2 — La vallée\n\n{FR}\n",
                    encoding="utf-8")
    config = {"data_dir": tmp_path, "episodes_dir": tmp_path / "episodes",
              "audio_dir": tmp_path / "audio", "images_dir": tmp_path / "images",
              "output_dir": tmp_path / "output", "tts_engine": "silent",
              "resolution": (320, 180), "preset": "ultrafast", "words_per_minute": 600,
              "min_words": 0, "language": "ar", "auto_presentation": False,
              "image_prompts": "off", "visual_style": "slides"}
    manifest_path = main.main(book, config)
    assert manifest_path.parent.name == "montagne-ar"  # série distincte de la version française
    manifest = json.loads(manifest_path.read_text())
    assert manifest["language"] == "ar" and manifest["book"]["language"] == "fr"
    assert manifest["book"]["title"] == "عنوان 1"  # titre du livre traduit
    first = manifest["episodes"][0]
    assert first["title"] == "عنوان 1" or first["title"].startswith("عنوان")
    assert "الحلقة" in first["youtube_title"]
    assert "ترجمة للنص الأصلي" in first["description"]
    srt = (manifest_path.parent / first["subtitles"]).read_text(encoding="utf-8")
    assert "صعد الشيخ" in srt and "vieil homme" not in srt
    assert calls.count("la traduction des titres") == 2
    # Régénération : tout vient du cache, Claude n'est plus appelé pour traduire.
    calls.clear()
    main.main(book, config, force=True)
    assert not [c for c in calls if "traduction" in c]


def test_same_language_is_not_translated(monkeypatch):
    monkeypatch.setattr(claude_client, "available", lambda: True)
    assert main.book_language(FR, {"language": "fr"}) == ("fr", False)
    assert main.book_language(FR, {"language": "ar"}) == ("fr", True)
    assert main.book_language(FR, {"language": "ar", "book_language": "ar"}) == ("ar", False)
    monkeypatch.setattr(claude_client, "available", lambda: False)
    assert main.book_language(FR, {"language": "ar"}) == ("fr", False)  # sans clé : lu tel quel


def test_series_slug():
    assert main.series_slug("Le Collier", "x.pdf", "fr", "fr") == "le-collier"
    assert main.series_slug("Le Collier", "x.pdf", "ar", "fr") == "le-collier-ar"
    assert main.series_slug("الرجل والجبل", "rif.pdf", "ar", "ar") == "rif"
    assert main.series_slug("الرجل", "كتاب.pdf", "ar", "ar").startswith("livre-")


def test_translation_is_chunked_and_cached(tmp_path, monkeypatch):
    sent = []

    def ask(system, prompt, config, client=None, schema=None, what="", max_tokens=16000):
        sent.append(prompt)
        return "ترجمة"

    monkeypatch.setattr(claude_client, "ask", ask)
    long_text = "\n\n".join(["mot " * 1000] * 4)
    ep = Episode(1, "Chapitre 1", long_text)
    out = translator.translate_episode(ep, {"language": "ar"}, source="fr", book_title="L",
                                       cache_dir=tmp_path)
    assert len(sent) == 2 and out.text == "ترجمة\n\nترجمة"
    assert "du français vers l'arabe" in sent[0]
    again = translator.translate_episode(ep, {"language": "ar"}, source="fr", book_title="L",
                                         cache_dir=tmp_path)
    assert again.text == out.text and len(sent) == 2
