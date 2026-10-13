from modules import cleaner, scripter, splitter

CONFIG = {"split_mode": "chapter", "target_minutes": 10, "max_minutes": 20,
          "words_per_minute": 150, "min_words": 0, "language": "fr"}


def test_clean_removes_headers_page_numbers_and_hyphenation():
    pages = []
    for n in range(1, 6):
        pages.append(f"Mon Livre — Auteur\nLigne {'x' * n} de la page.\nLe héros mar-\n"
                     f"chait dans la forêt sombre\net froide.\nFin {'y' * n} de page.\n\n{n}")
    text = cleaner.clean("\f".join(pages))
    assert "Mon Livre" not in text
    assert text.count("marchait dans la forêt sombre et froide.") == 5
    assert not any(line.strip().isdigit() for line in text.split("\n"))


def test_clean_keeps_headings_as_paragraphs():
    text = cleaner.clean("Chapitre 1\nIl était une fois.\n\nChapitre 2\nLa suite.")
    assert text.split("\n\n") == ["Chapitre 1", "Il était une fois.", "Chapitre 2", "La suite."]


def test_split_by_chapter():
    body = " ".join(["Une phrase de test assez banale."] * 40)
    text = "\n\n".join(["Chapitre 1", body, "Chapitre 2", body, "Chapitre 3", body])
    episodes = splitter.split(text, CONFIG)
    assert [e.title for e in episodes] == ["Chapitre 1", "Chapitre 2", "Chapitre 3"]


def test_long_chapter_is_split_in_balanced_parts():
    para = " ".join(["Une phrase de test assez banale."] * 20)  # 100 mots
    text = "\n\n".join(["Chapitre 1"] + [para] * 50)  # 5000 mots > 20 min * 150
    episodes = splitter.split(text, CONFIG)
    assert [e.title for e in episodes] == ["Chapitre 1 (1/2)", "Chapitre 1 (2/2)"]
    assert abs(episodes[0].word_count - episodes[1].word_count) <= 100


def test_split_by_words_without_chapters():
    para = " ".join(["Mot"] * 99) + "."
    text = "\n\n".join([para] * 10)  # 990 mots
    episodes = splitter.split(text, {**CONFIG, "split_mode": "words", "target_words": 300})
    assert len(episodes) == 3
    assert episodes[0].title == "Partie 1"
    assert sum(e.word_count for e in episodes) == 990


def test_chapter_mode_falls_back_to_duration():
    para = " ".join(["Mot"] * 99) + "."
    text = "\n\n".join([para] * 30)  # 2970 mots, pas de chapitre
    episodes = splitter.split(text, {**CONFIG, "target_minutes": 10})
    assert len(episodes) == 2


def test_script_segments_are_bounded():
    ep = splitter.Episode(1, "Chapitre 1", "M. Dupont arriva[2]. " + "Une longue phrase, " * 40 + "fin.")
    script = scripter.make_script(ep, {"segment_max_chars": 120}, book_title="Livre",
                                  total_episodes=1)
    assert script.segments[0].kind == "intro"
    assert script.segments[-1].kind == "outro" and "fin du livre" in script.segments[-1].text
    assert all(len(s.text) <= 120 for s in script.segments if s.kind == "body")
    assert script.segments[1].text.startswith("Monsieur Dupont arriva.")


def test_table_of_contents_is_not_read_nor_used_as_title():
    body = " ".join(["Une phrase de test assez banale."] * 40)
    raw = "\n".join([
        "Table des matières",
        "Chapitre premier Sur la plage de Kirkcaple............ 5",
        "Chapitre II Vers l’inconnu . . . . . . . . . . 21",
        "Chapitre III Blaauwildebeestefontein……… 40",
        "", "Chapitre premier", "", "Sur la plage de Kirkcaple", "", body,
        "", "Chapitre II", "", "Vers l’inconnu", "", body,
    ])
    text = cleaner.clean(raw)
    assert "Kirkcaple....." not in text and "21" not in text
    episodes = splitter.split(text, CONFIG)
    assert [e.title for e in episodes] == [
        "Chapitre premier — Sur la plage de Kirkcaple", "Chapitre II — Vers l’inconnu"]


def test_heading_run_without_body_keeps_only_last():
    body = " ".join(["Une phrase de test assez banale."] * 40)
    text = "\n\n".join(["Chapitre I Le début", "Chapitre II La suite", "Chapitre III La fin", body])
    (episode,) = splitter.split(text, CONFIG)
    assert episode.title == "Chapitre III La fin"
