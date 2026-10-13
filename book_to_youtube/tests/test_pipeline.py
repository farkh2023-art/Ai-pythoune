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
