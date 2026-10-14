from test_prompts import FakeClaude

import main
from modules import branding


def cfg(tmp_path, **extra):
    return {"data_dir": tmp_path, "episodes_dir": tmp_path / "episodes", "language": "fr", **extra}


def test_claude_writes_a_clean_presentation(tmp_path):
    client = FakeClaude(["« **Écosse, 1900.** Un jeune homme part pour l'Afrique.\n\nLe danger "
                         "l'attend. »"])
    text = branding.generate_presentation(cfg(tmp_path), book_title="Le Collier",
                                          author="John Buchan", text="Tout le livre.",
                                          channel_name="Le Grenier du Savoir", client=client)
    assert text == "Écosse, 1900. Un jeune homme part pour l'Afrique. Le danger l'attend."
    prompt = client.calls[0]["messages"][0]["content"]
    assert "« Le Collier », de John Buchan" in prompt and "Le Grenier du Savoir" in prompt
    assert "Ne révèle ni la fin" in prompt and "Tout le livre." in prompt


def test_auto_presentation_only_when_missing(tmp_path):
    config = cfg(tmp_path)
    args = dict(book_title="Livre", author=None, text="Texte.", log=lambda *_: None)
    client = FakeClaude(["Une présentation."])
    assert branding.ensure_presentation(config, "livre", client=client, **args) == "Une présentation."
    assert branding.load_presentation(config, "livre") == "Une présentation."
    # Déjà présente (ou modifiée par le créateur) : jamais réécrite.
    branding.save_presentation(config, "livre", "Ma version.")
    assert branding.ensure_presentation(config, "livre", client=FakeClaude([]), **args) == "Ma version."
    # Désactivée, ou sans clé API : rien n'est demandé.
    assert branding.ensure_presentation({**config, "auto_presentation": False}, "autre",
                                        client=FakeClaude([]), **args) == ""
    assert branding.ensure_presentation(config, "autre", **args) == ""


def test_api_error_does_not_block_the_build(tmp_path):
    class Broken:
        def __getattr__(self, name):
            raise ConnectionError("réseau coupé")
    messages = []
    assert branding.ensure_presentation(cfg(tmp_path), "livre", book_title="L", author=None,
                                        text="T", client=Broken(), log=messages.append) == ""
    assert "impossible" in messages[0]


def test_cli_presentation_command(tmp_path):
    book = tmp_path / "mon_livre.txt"
    book.write_text("Chapitre 1\n\nIl était une fois.", encoding="utf-8")
    config = cfg(tmp_path)
    client = FakeClaude(["Première version.", "Seconde version."])
    assert main.write_presentation(book, config, client=client) == "Première version."
    assert main.write_presentation(book, config, client=client) == "Première version."  # gardée
    assert main.write_presentation(book, config, force=True, client=client) == "Seconde version."


def test_intro_card_shows_presentation_on_a_dark_panel():
    from modules import visuals
    config = {"resolution": (1280, 720), "language": "fr"}
    text = "Dans les montagnes du Rif, un homme bâtit une cabane au sommet. " * 4
    for cls in (visuals.SlideRenderer, visuals.OverlayRenderer):
        card = cls(config, "Le Livre", 1, "Le sage du sommet", presentation=text).intro_card()
        bare = cls(config, "Le Livre", 1, "Le sage du sommet").intro_card()
        assert card.size == (1280, 720) and card.mode == bare.mode
        assert card.tobytes() != bare.tobytes()
