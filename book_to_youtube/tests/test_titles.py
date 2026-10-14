import json

from test_prompts import FakeClaude

from modules import titles
from modules.splitter import Episode

EPS = [Episode(1, "Partie 1", "Ateeq monte au sommet et bâtit sa cabane."),
       Episode(2, "Partie 2", "La famine frappe ; il nourrit les enfants.")]


def test_claude_titles_replace_default_ones_and_are_cached(tmp_path):
    client = FakeClaude([json.dumps({"titles": ["« Le serment du sommet »", "Épisode 2 : La famine."]})])
    named = titles.name_episodes(EPS, {"language": "fr"}, book_title="L'homme", cache_dir=tmp_path,
                                 client=client, log=lambda *_: None)
    assert [e.title for e in named] == ["Le serment du sommet", "La famine"]
    assert [e.text for e in named] == [e.text for e in EPS]
    prompt = client.calls[0]["messages"][0]["content"]
    assert "« L'homme »" in prompt and "2 à 6 mots" in prompt and "La famine frappe" in prompt
    # Cache : même texte, mêmes titres, sans nouvel appel.
    again = titles.name_episodes(EPS, {"language": "fr"}, book_title="L'homme",
                                 cache_dir=tmp_path, client=FakeClaude([]))
    assert [e.title for e in again] == ["Le serment du sommet", "La famine"]


def test_chapter_titles_and_no_key_are_left_alone(tmp_path):
    chapters = [Episode(1, "Chapitre I — Le départ", "Texte.")]
    assert titles.name_episodes(chapters, {}, book_title="L", cache_dir=tmp_path,
                                client=FakeClaude([])) == chapters
    # Sans clé API : « Partie N » est gardé.
    assert titles.name_episodes(EPS, {}, book_title="L", cache_dir=tmp_path) == EPS
    assert titles.name_episodes(EPS, {"episode_titles": "off"}, book_title="L",
                                cache_dir=tmp_path, client=FakeClaude([])) == EPS


def test_bad_answer_keeps_default_titles(tmp_path):
    client = FakeClaude([json.dumps({"titles": ["Un seul"]})])
    messages = []
    named = titles.name_episodes(EPS, {}, book_title="L", cache_dir=tmp_path, client=client,
                                 log=messages.append)
    assert named == EPS and "impossible" in messages[0]
    assert not (tmp_path / titles.CACHE_NAME).exists()
