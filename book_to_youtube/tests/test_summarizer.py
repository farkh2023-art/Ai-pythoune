from types import SimpleNamespace

import pytest

from modules import summarizer
from modules.splitter import Episode

CONFIG = {"language": "fr", "summary_minutes": 1, "words_per_minute": 100}
TEXT = " ".join(["Le héros marche longtemps dans la forêt."] * 60)  # 420 mots


class FakeClient:
    """Imite client.beta.messages.stream(...) du SDK anthropic."""

    def __init__(self, text="## Titre\n\n- Le héros **part**.\n\n\nIl revient.", stop_reason="end_turn"):
        self.calls = []
        self.response = SimpleNamespace(
            stop_reason=stop_reason,
            stop_details=SimpleNamespace(explanation="test"),
            content=[SimpleNamespace(type="thinking", thinking=""),
                     SimpleNamespace(type="text", text=text)],
        )
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        response = self.response

        class Stream:
            def __enter__(self):
                return SimpleNamespace(get_final_message=lambda: response)

            def __exit__(self, *exc):
                return False

        return Stream()


def test_summary_request_and_cleanup(tmp_path):
    client = FakeClient()
    ep = Episode(2, "Chapitre 2", TEXT)
    out = summarizer.summarize_episode(ep, CONFIG, book_title="Livre", author="Auteur",
                                       total_episodes=5, cache_dir=tmp_path, client=client)
    assert out.text == "Titre\n\nLe héros part.\n\nIl revient."
    assert (out.number, out.title) == (2, "Chapitre 2")

    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["thinking"] == {"type": "adaptive"}
    assert call["fallbacks"] == "default" and call["betas"] == [summarizer.FALLBACK_BETA]
    prompt = call["messages"][0]["content"]
    assert "épisode 2 sur 5" in prompt and "« Livre », de Auteur" in prompt
    assert "Environ 100 mots" in prompt and TEXT in prompt


def test_summary_is_cached_and_editable(tmp_path):
    ep = Episode(1, "Chapitre 1", TEXT)
    client = FakeClient()
    summarizer.summarize_episode(ep, CONFIG, book_title="L", cache_dir=tmp_path, client=client)
    (cached,) = tmp_path.glob("episode_001.resume.*.txt")
    cached.write_text("Version corrigée par le créateur.", encoding="utf-8")

    again = summarizer.summarize_episode(ep, CONFIG, book_title="L", cache_dir=tmp_path,
                                         client=client)
    assert again.text == "Version corrigée par le créateur."
    assert len(client.calls) == 1  # aucun nouvel appel à l'API


def test_refusal_raises():
    with pytest.raises(summarizer.SummaryError):
        summarizer.summarize_episode(Episode(1, "C", TEXT), CONFIG, book_title="L",
                                     client=FakeClient(stop_reason="refusal"))


def test_target_never_exceeds_original():
    short = Episode(1, "C", "Une phrase très courte de sept mots.")
    assert summarizer.target_words(short, {"summary_minutes": 5}) == 50
    assert summarizer.target_words(Episode(1, "C", TEXT), CONFIG) == 100
