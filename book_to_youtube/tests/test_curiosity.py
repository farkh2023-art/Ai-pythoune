import json
import shutil
import subprocess

import pytest
from test_prompts import FakeClaude

import main
from modules import curiosity, reader, splitter, visuals

QUESTIONS = [
    {"angle": "pourquoi", "question": "Savez-vous pourquoi on sursaute avant de dormir ?",
     "script": "Savez-vous pourquoi on sursaute avant de dormir ? C'est la secousse hypnique. "
               "Les muscles se relâchent d'un coup. Ça vous arrive ?",
     "titre_youtube": "Pourquoi sursaute-t-on avant de dormir ?",
     "hashtags": ["#sommeil", "cerveau", "science"], "a_verifier": "Hypothèse : Inserm."},
    {"angle": "saviez-vous", "question": "Saviez-vous qu'on dort un tiers de sa vie ?",
     "script": "Saviez-vous qu'on dort un tiers de sa vie ? Environ vingt-cinq ans.",
     "titre_youtube": "Un tiers de sa vie à dormir ?", "hashtags": ["sommeil"],
     "a_verifier": "Durées : Inserm."},
    {"angle": "comment", "question": "Savez-vous comment le cerveau se nettoie la nuit ?",
     "script": "Savez-vous comment le cerveau se nettoie la nuit ? Le système glymphatique.",
     "titre_youtube": "Le cerveau fait le ménage la nuit", "hashtags": ["cerveau"],
     "a_verifier": "Études chez la souris."},
    {"angle": "quoi", "question": "Savez-vous ce qu'est la paralysie du sommeil ?",
     "script": "Savez-vous ce qu'est la paralysie du sommeil ? Un réveil sans pouvoir bouger.",
     "titre_youtube": "La paralysie du sommeil, c'est quoi ?", "hashtags": ["sommeil"],
     "a_verifier": "Pas de conseil médical."},
]


def answer():
    return json.dumps({"titre": "Le sommeil en 4 questions", "questions": QUESTIONS})


def test_claude_writes_four_angles(tmp_path):
    client = FakeClaude([answer()])
    data = curiosity.generate("sommeil", {"language": "ar", "curiosity_words": 100},
                              domain="santé", channel_name="Le Grenier du Savoir",
                              client=client)
    assert [q["angle"] for q in data["questions"]] == list(curiosity.ANGLES)
    assert data["questions"][0]["hashtags"] == ["sommeil", "cerveau", "science"]
    assert data["langue"] == "ar" and data["titre"] == "Le sommeil en 4 questions"
    prompt = client.calls[0]["messages"][0]["content"]
    assert "« sommeil » (domaine : santé)" in prompt and "en arabe" in prompt
    assert "environ 100 mots" in prompt and "Exactitude" in prompt and "légende" in prompt
    path = curiosity.save(data, tmp_path)
    assert path.name == "sommeil-ar.curiosite.json" and curiosity.load(path) == data
    # Un deuxième tirage sur le même mot-clé n'écrase pas le premier (déjà relu).
    again = curiosity.save({**data, "titre": "autre"}, tmp_path)
    assert again.name == "sommeil-ar-2.curiosite.json" and curiosity.load(path) == data


def test_too_few_questions_is_an_error():
    client = FakeClaude([json.dumps({"titre": "x", "questions": QUESTIONS[:2]})])
    with pytest.raises(Exception, match="2 question"):
        curiosity.generate("café", {}, client=client)


def write_file(tmp_path, lang="fr"):
    data = {"mot_cle": "sommeil", "domaine": "", "langue": lang,
            "titre": "Le sommeil en 4 questions", "questions": QUESTIONS}
    return curiosity.save(data, tmp_path)


def test_file_is_read_as_a_book(tmp_path):
    book = reader.load_book(write_file(tmp_path))
    assert book.title == "Le sommeil en 4 questions" and book.metadata["curiosite"]
    eps = splitter.split(book.text, {"split_mode": "chapter", "min_words": 0})
    assert len(eps) == 4
    assert eps[0].title == "Question 1 — Savez-vous pourquoi on sursaute avant de dormir ?"
    assert eps[0].text.startswith("Savez-vous pourquoi on sursaute")
    assert curiosity.question_of(eps[0].title) == QUESTIONS[0]["question"]
    assert curiosity.question_of("السؤال 2 — هل تعلم؟") == "هل تعلم؟"
    (single,) = splitter.split(book.text, {"split_mode": "single", "single_title": book.title})
    assert single.title == book.title and "Question 1" not in single.text
    assert single.text.count("Savez-vous") == 3 and "Saviez-vous" in single.text


def test_other_json_files_are_refused(tmp_path):
    other = tmp_path / "data.json"
    other.write_text("{}")
    with pytest.raises(ValueError, match="curiosite"):
        reader.load_book(other)


def test_short_config():
    config = main.effective_config({"video_format": "short"})
    assert config["resolution"] == (1080, 1920) and config["add_intro"] is False
    assert main.effective_config({"video_format": "short",
                                  "resolution": (1280, 720)})["resolution"] == (720, 1280)
    assert main.effective_config({})["resolution"] == main.CONFIG["resolution"]


def test_short_frames_are_vertical():
    config = {"resolution": (540, 960), "video_format": "short", "language": "fr"}
    r = visuals.OverlayRenderer(config, "Sommeil", 1, "Question 1 — Savez-vous pourquoi… ?")
    assert r.question == "Savez-vous pourquoi… ?"
    assert r.caption_slide("Une réponse courte.", 0.5).size == (540, 960)
    assert r.end_slide(True).size == (540, 960)
    assert visuals.SlideRenderer(config, "S", 1, "Q").card_slide("x", 0.1).size == (540, 960)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg requis")
def test_build_four_shorts_and_one_long_video(tmp_path):
    path = write_file(tmp_path / "input")
    base = {"data_dir": tmp_path, "episodes_dir": tmp_path / "episodes",
            "audio_dir": tmp_path / "audio", "images_dir": tmp_path / "images",
            "output_dir": tmp_path / "output", "tts_engine": "silent", "preset": "ultrafast",
            "words_per_minute": 600, "visual_style": "card", "auto_presentation": False,
            "image_prompts": "off"}
    manifest = main.main(path, {**base, "video_format": "short", "resolution": (360, 640)})
    assert manifest.parent.name.endswith("-short")
    eps = json.loads(manifest.read_text())["episodes"]
    assert len(eps) == 4
    assert eps[0]["youtube_title"] == "Pourquoi sursaute-t-on avant de dormir ? #Shorts"
    assert eps[0]["description"].endswith("#sommeil #cerveau #science #Shorts")
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0",
                            str(manifest.parent / eps[0]["video"])],
                           capture_output=True, text=True, check=True)
    assert probe.stdout.strip() == "360,640"
    srt = (manifest.parent / eps[0]["subtitles"]).read_text(encoding="utf-8")
    assert "Bienvenue" not in srt and "Abonnez-vous" in srt  # pas de générique en Short

    long_ = main.main(path, {**base, "split_mode": "single", "resolution": (320, 180)})
    assert long_.parent.name.endswith("-longue")  # n'écrase ni les Shorts ni une autre série
    (ep,) = json.loads(long_.read_text())["episodes"]
    assert ep["youtube_title"] == "Le sommeil en 4 questions"
    assert "Épisode 1" not in (long_.parent / ep["subtitles"]).read_text(encoding="utf-8")

    # Questions en anglais, construites sans --lang : Shorts en anglais, dans leur dossier.
    english = write_file(tmp_path / "input", lang="en")
    manifest = main.main(english, {**base, "video_format": "short", "resolution": (360, 640)},
                         only=[1])
    assert manifest.parent.name == "sommeil-en-short"
    assert json.loads(manifest.read_text())["language"] == "en"


def test_video_is_in_the_language_of_the_questions(tmp_path, monkeypatch):
    from modules import claude_client
    monkeypatch.setattr(claude_client, "available", lambda: True)
    book = reader.load_book(write_file(tmp_path, lang="en"))
    text = book.text
    # Sans --lang : vidéo en anglais, rien à traduire (avant : retraduite en français).
    config = main.written_language(book, main.effective_config({}), {})
    assert config["language"] == "en" and main.book_language(text, config) == ("en", False)
    # --lang explicite : traduction depuis la langue des questions.
    config = main.written_language(book, main.effective_config({"language": "ar"}),
                                   {"language": "ar"})
    assert config["language"] == "ar" and main.book_language(text, config) == ("en", True)
    # Un livre ordinaire n'est pas concerné.
    plain = reader.Book(title="x", author=None, text="x", source=tmp_path / "x.txt")
    assert main.written_language(plain, {"language": "fr"}, {}) == {"language": "fr"}


def test_each_questions_file_has_its_own_series(tmp_path):
    first = write_file(tmp_path)
    second = write_file(tmp_path)  # même mot-clé, même titre : « sommeil-fr-2 »
    slugs = {main.series_slug("Le sommeil en 4 questions", path, "fr", "fr", short=True)
             for path in (first, second)}
    assert slugs == {"sommeil-fr-short", "sommeil-fr-2-short"}
    assert main.series_slug("نوم", tmp_path / "question-ar.curiosite.json", "ar", "ar") \
        == "question-ar"


def test_write_curiosity_saves_into_input(tmp_path, monkeypatch):
    monkeypatch.setitem(main.CONFIG, "input_dir", tmp_path)
    monkeypatch.setitem(main.CONFIG, "data_dir", tmp_path)
    path = main.write_curiosity("foudre", "nature", "fr", client=FakeClaude([answer()]),
                                log=lambda *_: None)
    assert path == tmp_path / "foudre-fr.curiosite.json"
