import json
import shutil
import subprocess

import pytest

import main
from modules import uploader

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg requis")


@pytest.fixture
def config(tmp_path):
    dirs = {k: tmp_path / k for k in ("episodes_dir", "audio_dir", "images_dir", "output_dir")}
    return {**dirs, "tts_engine": "silent", "resolution": (640, 360), "preset": "ultrafast",
            "words_per_minute": 600, "min_words": 0}


@pytest.fixture
def manifest(tmp_path, config):
    body = " ".join(["Une phrase de test assez banale."] * 30)
    book = tmp_path / "mon_livre.txt"
    book.write_text(f"Chapitre 1\n\n{body}\n\nChapitre 2\n\n{body}\n", encoding="utf-8")
    return main.main(book, config)


def test_build_produces_videos_pending_review(manifest):
    data = uploader.load_manifest(manifest)
    assert [e["status"] for e in data["episodes"]] == ["a_valider", "a_valider"]
    for ep in data["episodes"]:
        video = manifest.parent / ep["video"]
        duration = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
             str(video)], capture_output=True, text=True, check=True).stdout)
        assert abs(duration - ep["duration"]) < 0.3
        assert (manifest.parent / ep["subtitles"]).read_text().startswith("1\n00:00:00,000")


class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result

    def next_chunk(self):
        return None, self.result


class FakeYouTube:
    def __init__(self):
        self.uploaded = []

    def __getattr__(self, name):
        return lambda: self

    def insert(self, part, body, media_body=None):
        if "privacyStatus" in body.get("status", {}) and "title" in body["snippet"] and media_body:
            self.uploaded.append(body["snippet"]["title"])
            return FakeRequest({"id": f"vid{len(self.uploaded)}"})
        return FakeRequest({"id": "playlist1"})

    def set(self, **kwargs):
        return FakeRequest({})


def test_upload_requires_creator_approval(manifest):
    youtube = FakeYouTube()
    assert uploader.upload(manifest, {}, service=youtube, log=lambda *_: None) == []
    with pytest.raises(PermissionError):
        uploader.upload(manifest, {}, numbers=[1], service=youtube, log=lambda *_: None)

    uploader.set_status(manifest, [2], uploader.APPROVED)
    ids = uploader.upload(manifest, {}, service=youtube, log=lambda *_: None)
    assert ids == ["vid1"] and len(youtube.uploaded) == 1
    data = uploader.load_manifest(manifest)
    assert [e["status"] for e in data["episodes"]] == ["a_valider", "publie"]
    assert data["playlist_id"] == "playlist1"


def test_interactive_review(manifest):
    answers = iter(["v", "r", "trop long"])
    uploader.review(manifest, input_fn=lambda _: next(answers), print_fn=lambda *_: None)
    data = uploader.load_manifest(manifest)
    assert [e["status"] for e in data["episodes"]] == ["valide", "rejete"]
    assert data["episodes"][1]["review_note"] == "trop long"


def test_parse_numbers():
    assert main.parse_numbers("1,3,5-7") == [1, 3, 5, 6, 7]


def test_build_in_summary_mode(tmp_path, config, monkeypatch):
    monkeypatch.setattr("modules.summarizer._call_claude",
                        lambda system, prompt, cfg, client=None: "Résumé court du chapitre.")
    book = tmp_path / "livre.txt"
    book.write_text("Chapitre 1\n\n" + " ".join(["Une phrase assez banale."] * 50), encoding="utf-8")
    manifest = main.main(book, {**config, "narration_mode": "resume"})
    (ep,) = uploader.load_manifest(manifest)["episodes"]
    assert ep["narration"] == "resume" and ep["words"] == 4
    assert "IA" in ep["description"]


def test_max_episodes_keeps_series_numbering(tmp_path, config):
    body = " ".join(["Une phrase de test assez banale."] * 30)
    book = tmp_path / "livre.txt"
    book.write_text("\n\n".join(f"Chapitre {i}\n\n{body}" for i in (1, 2, 3)), encoding="utf-8")
    manifest = main.main(book, {**config, "max_episodes": 1})
    (ep,) = uploader.load_manifest(manifest)["episodes"]
    assert "1/3" in ep["description"]


@pytest.mark.parametrize("ken_burns", [True, False])
def test_build_illustrated_style(tmp_path, config, monkeypatch, ken_burns):
    from test_illustrations import FakeCommons, page
    from modules import illustrations

    fake = FakeCommons({"phrase": [page(1, "Une phrase.jpg", index=1),
                                   page(2, "Autre phrase.jpg", index=2)]})
    real_client = illustrations.CommonsClient
    monkeypatch.setattr(illustrations, "CommonsClient",
                        lambda cache_dir, width=1920: real_client(cache_dir, width, fetch=fake))
    body = " ".join(["Une phrase assez banale sur la plage."] * 60)
    book = tmp_path / "livre.txt"
    book.write_text(f"Chapitre 1\n\n{body}", encoding="utf-8")
    manifest = main.main(book, {**config, "visual_style": "illustre", "scene_seconds": 10,
                                "ken_burns": ken_burns})
    (ep,) = uploader.load_manifest(manifest)["episodes"]
    video = manifest.parent / ep["video"]
    duration = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(video)], capture_output=True, text=True, check=True).stdout)
    assert abs(duration - ep["duration"]) < 0.3
    assert len(ep["image_credits"]) == 2
    assert "Wikimedia Commons" in ep["description"] and "Jean Photo" in ep["description"]


def test_reusable_build_options():
    argv = ["build", "--no-zoom", "data/input/x.pdf", "--style", "illustre", "--episodes", "3",
            "--force", "--theme", "South Africa", "--max-episodes=2"]
    assert main._reusable_options(argv, "data/input/x.pdf") == [
        "--no-zoom", "--style", "illustre", "--theme", "South Africa"]


def test_prompts_then_personal_image_in_video(tmp_path, config, monkeypatch):
    """Import → prompts → image déposée pour la scène 2 → vidéo : l'image y est bien."""
    from PIL import Image

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    data = tmp_path / "data"
    cfg = {**config, "data_dir": data, "visual_style": "illustre", "ken_burns": False,
           "illustration_source": "dossier", "scene_seconds": 20, "words_per_minute": 300}
    body = " ".join(["Une phrase assez banale sur la plage."] * 60)
    book = tmp_path / "livre.txt"
    book.write_text(f"Chapitre 1\n\n{body}", encoding="utf-8")

    main.main(book, cfg, prompts_only=True)
    folder = data / "illustrations" / "livre" / "episode_001"
    scenes = json.loads((folder / "scenes.json").read_text())["scenes"]
    assert len(scenes) >= 3 and (data / "illustrations/livre/PROMPTS.md").exists()
    assert not list((tmp_path / "output_dir" / "livre").glob("*.mp4"))  # pas de vidéo

    Image.new("RGB", (1280, 720), (255, 0, 0)).save(folder / "scene_02.jpg")  # rouge vif
    manifest = main.main(book, cfg)
    (ep,) = uploader.load_manifest(manifest)["episodes"]
    assert ep["image_credits"] == []  # vos images ne demandent pas de crédit

    # Image du milieu de la scène 2 : le fond (hors bandeau) doit être rouge.
    video = manifest.parent / ep["video"]
    frame = tmp_path / "frame.png"
    # 2e scène ≈ entre 20 et 40 s de texte estimé, après un générique de quelques secondes.
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", "30", "-i", str(video),
                    "-frames:v", "1", str(frame)], check=True)
    r, g, b = Image.open(frame).convert("RGB").getpixel((600, 200))
    assert r > 180 and g < 80 and b < 80, (r, g, b)


def test_manual_upload_export_and_mark_published(manifest, tmp_path):
    with pytest.raises(PermissionError):  # pas encore validé
        uploader.export_for_manual_upload(manifest, 1, tmp_path / "dl")
    uploader.set_status(manifest, [1], uploader.APPROVED)
    out = uploader.export_for_manual_upload(manifest, 1, tmp_path / "dl")
    folder = tmp_path / "dl" / manifest.parent.name / "episode_001"
    assert out["folder"] == str(folder)
    assert sorted(p.name for p in folder.iterdir()) == [
        "miniature.jpg", "sous-titres.srt", "titre_description.txt", "video.mp4"]
    assert out["title"] in (folder / "titre_description.txt").read_text()

    with pytest.raises(ValueError):
        uploader.mark_published(manifest, 1, "https://www.youtube.com/@ma_chaine")
    with pytest.raises(PermissionError):
        uploader.mark_published(manifest, 2, "https://youtu.be/dQw4w9WgXcQ")  # non validé
    ep = uploader.mark_published(manifest, 1, "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5")
    assert (ep["status"], ep["youtube_id"], ep["manual_upload"]) == ("publie", "dQw4w9WgXcQ", True)
    # Un épisode publié à la main n'est jamais renvoyé par l'envoi automatique.
    assert uploader.upload(manifest, {}, service=object(), log=lambda *_: None) == []


@pytest.mark.parametrize("link", ["https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ",
                                  "https://youtube.com/shorts/dQw4w9WgXcQ?si=abc",
                                  "https://studio.youtube.com/video/dQw4w9WgXcQ/edit",
                                  "https://m.youtube.com/watch?feature=share&v=dQw4w9WgXcQ"])
def test_parse_youtube_links(link):
    assert uploader.parse_youtube_id(link) == "dQw4w9WgXcQ"


def test_scanned_pdf_gives_a_clear_ocr_message(tmp_path, config):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for _ in range(5):  # pages sans texte, comme un livre scanné
        doc.new_page().draw_rect((50, 50, 300, 400), color=(0, 0, 0), fill=(0.8, 0.8, 0.8))
    doc.save(tmp_path / "scan.pdf")
    with pytest.raises(SystemExit, match="scanné.*OCR"):
        main.main(tmp_path / "scan.pdf", config)
