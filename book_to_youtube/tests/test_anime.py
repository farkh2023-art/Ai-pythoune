import json
import shutil
import subprocess

import pytest
from test_prompts import FakeClaude

import main
from modules import illustrations, prompts, uploader, video
from modules.splitter import Episode

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg requis")

EP = Episode(1, "Le départ", "texte")
SCENES = [{"scene": 1, "prompt": "A man at his desk.", "extrait": "Il s'assoit.",
           "texte": "Il s'assoit à son bureau."},
          {"scene": 2, "prompt": "A blue window on screen.", "extrait": "Une fenêtre.",
           "texte": "Une fenêtre bleue apparaît."}]


def make_clip(path, seconds=1.0, size="320x240"):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc=size={size}:rate=25:duration={seconds}", "-f", "lavfi", "-i",
                    f"sine=duration={seconds}", "-shortest", "-pix_fmt", "yuv420p", str(path)],
                   check=True)
    return path


def frames_of(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                          "-show_entries", "stream=nb_read_frames,width,height",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)["streams"][0]


def test_motion_prompts(tmp_path):
    config = {"data_dir": tmp_path}
    client = FakeClaude([json.dumps({"prompts": ["Slow push-in on the man.",
                                                 "The window glows and pulses."]})])
    motions = prompts.write_motion_prompts(config, "livre", EP, SCENES, book_title="Jarvis",
                                           client=client, log=lambda *_: None)
    assert motions == ["Slow push-in on the man.", "The window glows and pulses."]
    prompt = client.calls[0]["messages"][0]["content"]
    assert "Une fenêtre bleue apparaît." in prompt and "A blue window on screen." in prompt
    folder = illustrations.episode_dir(config, "livre", 1)
    txt = (folder / "prompts_animation.txt").read_text()
    assert "scene_02.mp4" in txt and "The window glows" in txt
    # Cache : mêmes scènes, aucun nouvel appel.
    assert prompts.write_motion_prompts(config, "livre", EP, SCENES, book_title="Jarvis",
                                        client=FakeClaude([])) == motions
    # État des scènes pour le tableau de bord.
    (folder / "scenes.json").write_text(json.dumps({"scenes": [dict(s) for s in SCENES]}))
    make_clip(folder / "scene_02.mov")
    status = prompts.scene_status(config, "livre", 1)
    assert [s["animation"] for s in status] == motions
    assert [s["clip"] for s in status] == [None, "scene_02.mov"]
    # Remplacer un clip retire l'ancien, quel que soit son format.
    target = prompts.save_scene_clip(config, "livre", 1, 2, ".MP4")
    assert target.name == "scene_02.mp4" and not (folder / "scene_02.mov").exists()
    with pytest.raises(ValueError):
        prompts.save_scene_clip(config, "livre", 1, 2, ".gif")


def test_motion_prompts_fallback_without_claude(tmp_path):
    motions = prompts.write_motion_prompts({"data_dir": tmp_path, "image_prompts": "off"},
                                           "livre", EP, SCENES, book_title="J")
    assert len(motions) == 2 and "A blue window on screen." in motions[1]
    assert "no text" in motions[0]


@pytest.mark.parametrize("fill", ["aller-retour", "boucle", "fige"])
def test_short_clip_fills_its_whole_scene(tmp_path, fill):
    clip = make_clip(tmp_path / "c.mp4", seconds=1.0)
    out = tmp_path / "out.mp4"
    video._clip_scene("ffmpeg", clip, 80, 320, 180, 25, fill, out, tmp_path)
    info = frames_of(out)
    assert int(info["nb_read_frames"]) == 80 and (info["width"], info["height"]) == (320, 180)


def test_build_with_animated_and_still_scenes(tmp_path):
    body = " ".join(["Une phrase de test assez banale."] * 40)
    book = tmp_path / "livre.txt"
    book.write_text(f"Chapitre 1\n\n{body}\n", encoding="utf-8")
    config = {"data_dir": tmp_path, "episodes_dir": tmp_path / "episodes",
              "audio_dir": tmp_path / "audio", "images_dir": tmp_path / "images",
              "output_dir": tmp_path / "output", "tts_engine": "silent",
              "resolution": (320, 180), "preset": "ultrafast", "words_per_minute": 600,
              "min_words": 0, "visual_style": "illustre", "visual_mode": "anime",
              "illustration_source": "dossier", "scene_seconds": 4, "ken_burns": False,
              "image_prompts": "off", "add_intro": False}
    main.main(book, config, prompts_only=True)
    folder = illustrations.episode_dir(config, "livre", 1)
    assert (folder / "prompts_animation.txt").exists()
    scenes = json.loads((folder / "scenes.json").read_text())["scenes"]
    assert len(scenes) >= 2
    make_clip(folder / "scene_01.mp4", seconds=1.0, size="640x480")  # 4:3, recadré en 16:9
    from PIL import Image
    Image.new("RGB", (320, 180), "navy").save(folder / "scene_02.jpg")

    manifest = main.main(book, config, force=True)
    (ep,) = uploader.load_manifest(manifest)["episodes"]
    out = manifest.parent / ep["video"]
    info = frames_of(out)
    assert (info["width"], info["height"]) == (320, 180)
    assert abs(video.probe_duration(out) - ep["duration"]) < 0.5
