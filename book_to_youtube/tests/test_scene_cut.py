import json

from test_prompts import FakeClaude

from modules import illustrations, prompts
from modules.scripter import Segment
from modules.splitter import Episode

EP = Episode(1, "Partie 1", "texte")
SEGMENTS = [Segment("Bienvenue sur la chaîne.", "intro"),
            Segment("On ouvre le site TechEnClair dans le navigateur.", "body"),
            Segment("Le site demande de cocher la case « Je ne suis pas un robot ».", "body"),
            Segment("Windows Defender affiche alors une fenêtre bleue.", "body"),
            Segment("On clique sur « Exécuter quand même ».", "body"),
            Segment("Enfin, on prononce le mot Jarvis devant le micro.", "body"),
            Segment("À suivre.", "outro")]


def cfg(tmp_path, **extra):
    return {"data_dir": tmp_path, "language": "fr", **extra}


def answer(*scenes):
    return json.dumps({"scenes": [{"first": f, "prompt": p} for f, p in scenes]})


def test_claude_cuts_scenes_where_the_picture_changes(tmp_path):
    client = FakeClaude([answer((1, "A browser on the TechEnClair site."),
                                (3, "A blue Windows Defender window."),
                                (5, "A man speaking into a microphone."))])
    ranges, texts = prompts.visual_scene_plan(cfg(tmp_path), "livre", EP, SEGMENTS,
                                              book_title="Jarvis", client=client,
                                              log=lambda *_: None)
    # Indices de segments (le générique, indice 0, n'est jamais dans une scène).
    assert ranges == [(1, 2), (3, 4), (5, 5)]
    assert texts[1] == "A blue Windows Defender window."
    prompt = client.calls[0]["messages"][0]["content"]
    assert "[1] (" in prompt and "TechEnClair" in prompt and "entre 8 et 30 secondes" in prompt
    assert "Bienvenue" not in prompt
    # Cache : même narration, même découpage, sans nouvel appel ni clé.
    again = prompts.visual_scene_plan(cfg(tmp_path), "livre", EP, SEGMENTS, book_title="Jarvis",
                                      client=FakeClaude([]))
    assert again == (ranges, texts)
    # Les scènes minutées suivent ce découpage.
    timings = [type("T", (), {"start": i * 5.0, "end": i * 5.0 + 5, "text": s.text,
                              "kind": s.kind})() for i, s in enumerate(SEGMENTS)]
    scenes = illustrations.plan_scenes(timings, ranges)
    assert [(s.start, s.end) for s in scenes] == [(0.0, 15.0), (15.0, 25.0), (25.0, 35.0)]


def test_prompts_given_by_the_cut_are_written(tmp_path):
    config = cfg(tmp_path)
    ranges = [(1, 2), (3, 5)]
    texts = illustrations.scene_texts(SEGMENTS, ranges)
    scenes = prompts.write_episode_prompts(config, "livre", EP, texts, book_title="Jarvis",
                                           given=["First prompt.", "Second prompt."])
    assert [s["prompt"] for s in scenes] == ["First prompt.", "Second prompt."]
    assert "TechEnClair" in scenes[0]["extrait"]


def test_first_scene_always_starts_at_the_beginning(tmp_path):
    client = FakeClaude([answer((2, "B"), (2, "doublon"), (99, "hors limites"), (4, "D"))])
    ranges, texts = prompts.visual_scene_plan(cfg(tmp_path), "livre", EP, SEGMENTS,
                                              book_title="J", client=client, log=lambda *_: None)
    assert ranges == [(1, 3), (4, 5)] and texts == ["B", "D"]


def test_falls_back_to_duration_cut(tmp_path):
    kwargs = dict(book_title="J", log=lambda *_: None)
    # Mode « duree », pas de clé, ou réponse inutilisable : découpage par durée.
    assert prompts.visual_scene_plan(cfg(tmp_path, scene_cut="duree"), "livre", EP, SEGMENTS,
                                     client=FakeClaude([]), **kwargs) is None
    assert prompts.visual_scene_plan(cfg(tmp_path), "livre", EP, SEGMENTS, **kwargs) is None
    assert prompts.visual_scene_plan(cfg(tmp_path), "livre", EP, SEGMENTS,
                                     client=FakeClaude([answer()]), **kwargs) is None


def test_existing_user_images_keep_the_old_cut_unless_forced(tmp_path):
    folder = illustrations.episode_dir(cfg(tmp_path), "livre", 1)
    folder.mkdir(parents=True)
    (folder / "scene_01.jpg").write_bytes(b"jpg")
    messages = []
    assert prompts.visual_scene_plan(cfg(tmp_path), "livre", EP, SEGMENTS, book_title="J",
                                     client=FakeClaude([]), log=messages.append) is None
    assert "ancien découpage" in messages[0]
    forced = prompts.visual_scene_plan(cfg(tmp_path, scene_cut="claude"), "livre", EP,
                                       SEGMENTS, book_title="J",
                                       client=FakeClaude([answer((1, "A"))]),
                                       log=lambda *_: None)
    assert forced == ([(1, 5)], ["A"])
