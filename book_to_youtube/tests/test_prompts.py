import json
from types import SimpleNamespace

from modules import prompts
from modules.splitter import Episode


class FakeClaude:
    def __init__(self, texts):
        self.texts, self.calls = list(texts), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        text = self.texts.pop(0)
        response = SimpleNamespace(stop_reason="end_turn", stop_details=None,
                                   content=[SimpleNamespace(type="text", text=text)])

        class Stream:
            def __enter__(self):
                return SimpleNamespace(get_final_message=lambda: response)

            def __exit__(self, *exc):
                return False
        return Stream()


def cfg(tmp_path, **extra):
    return {"data_dir": tmp_path, "episodes_dir": tmp_path / "episodes", **extra}


EP = Episode(3, "Chapitre III", "texte")
TEXTS = ["Le navire quitta le port au lever du jour. La mer était grise.",
         "Dans la savane, le jeune homme marchait vers les collines."]


def test_template_prompts_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    scenes = prompts.write_episode_prompts(cfg(tmp_path), "livre", EP, TEXTS, book_title="Livre")
    assert [s["image"] for s in scenes] == ["scene_01.jpg", "scene_02.jpg"]
    assert "Le navire quitta le port" in scenes[0]["prompt"] and "16:9" in scenes[0]["prompt"]
    folder = tmp_path / "illustrations" / "livre" / "episode_003"
    txt = (folder / "prompts.txt").read_text()
    assert "Scène 02  →  scene_02.jpg" in txt and "Prompt :" in txt
    assert json.loads((folder / "scenes.json").read_text())["auteur"] == "modele"


def test_claude_prompts_with_visual_bible_and_cache(tmp_path):
    config = cfg(tmp_path, image_prompts="claude")
    client = FakeClaude(["Scotland, 1900s. David: 19, red hair, tweed jacket.",
                         json.dumps({"prompts": ["A ship leaving a grey harbour at dawn",
                                                 "A young red-haired man walking in the savanna"]})])
    bible = prompts.visual_bible(config, "livre", "Livre", "Auteur", "Tout le texte.", client=client)
    assert bible.startswith("Scotland") and (tmp_path / "illustrations/livre/bible.txt").exists()
    assert prompts.visual_bible(config, "livre", "Livre", None, "", client=client) == bible  # cache

    scenes = prompts.write_episode_prompts(config, "livre", EP, TEXTS, book_title="Livre",
                                           bible=bible, client=client)
    assert scenes[1]["prompt"] == "A young red-haired man walking in the savanna"
    request = client.calls[-1]
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert "red hair" in request["messages"][0]["content"]          # bible transmise
    assert 'scene numero="2"' in request["messages"][0]["content"]

    # Mêmes scènes : rien n'est redemandé. Scènes modifiées : nouveaux prompts.
    assert prompts.write_episode_prompts(config, "livre", EP, TEXTS, book_title="Livre",
                                         client=client) == scenes
    client.texts.append(json.dumps({"prompts": ["only one"]}))  # réponse incomplète
    changed = prompts.write_episode_prompts(config, "livre", EP, TEXTS + ["Une troisième scène."],
                                            book_title="Livre", client=client)
    assert changed[0]["prompt"] == "only one" and "troisième" in changed[2]["prompt"]


def test_scene_images_and_book_index(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = cfg(tmp_path)
    prompts.write_episode_prompts(config, "livre", EP, TEXTS, book_title="Livre")
    prompts.save_scene_image(config, "livre", 3, 2, b"img", ".jpeg")
    assert [s["fichier"] for s in prompts.scene_status(config, "livre", 3)] == [None, "scene_02.jpg"]
    prompts.save_scene_image(config, "livre", 3, 2, b"img", ".png")  # remplace
    assert prompts.scene_status(config, "livre", 3)[1]["fichier"] == "scene_02.png"
    index = prompts.write_book_index(config, "livre", "Livre").read_text()
    assert "⬜ **scene_01**" in index and "✅ **scene_02**" in index
    prompts.delete_scene_image(config, "livre", 3, 2)
    assert prompts.scene_status(config, "livre", 3)[1]["fichier"] is None
