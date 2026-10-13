"""Configuration centrale du pipeline livre -> vidéos YouTube.

Toutes les valeurs peuvent être surchargées depuis la ligne de commande
(voir ``main.py --help``) ou en modifiant ce dictionnaire.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

CONFIG = {
    # --- Dossiers de travail -------------------------------------------
    "data_dir": DATA_DIR,
    "input_dir": DATA_DIR / "input",
    "episodes_dir": DATA_DIR / "episodes",
    "audio_dir": DATA_DIR / "audio",
    "images_dir": DATA_DIR / "images",
    "output_dir": DATA_DIR / "output",

    # --- Langue ----------------------------------------------------------
    "language": "fr",  # "fr" ou "en" (textes d'intro/outro, voix par défaut)

    # --- Découpage en épisodes -------------------------------------------
    # "chapter"  : un épisode par chapitre détecté (repli sur "duration" si aucun)
    # "duration" : épisodes d'une durée cible (target_minutes)
    # "words"    : épisodes d'un nombre de mots cible (target_words)
    "split_mode": "chapter",
    "target_minutes": 10,
    "target_words": 1500,
    "words_per_minute": 150,  # débit moyen de narration, sert à estimer la durée
    # En mode "chapter", un chapitre plus long que max_minutes est redécoupé.
    "max_minutes": 20,
    # Un épisode plus court que min_words est fusionné avec le précédent.
    "min_words": 120,
    "max_episodes": None,  # ne générer que les N premiers épisodes (None = tous)

    # --- Narration ---------------------------------------------------------
    # "fidele" : lecture intégrale du texte
    # "resume" : chaque épisode est résumé par Claude (API Anthropic) avant la lecture
    "narration_mode": "fidele",
    "summary_minutes": 5,  # durée visée d'un épisode résumé
    "summary_model": "claude-opus-5-5",
    "summary_effort": "medium",  # "low", "medium", "high"…
    "add_intro": True,
    "add_outro": True,
    "segment_max_chars": 220,  # taille max d'un segment (diapo / sous-titre)

    # --- Synthèse vocale ---------------------------------------------------
    # "edge"    : edge-tts (en ligne, voix neuronales, recommandé)
    # "gtts"    : Google Translate TTS (en ligne)
    # "pyttsx3" : moteur hors-ligne du système (espeak / SAPI / NSSpeech)
    # "silent"  : silence calibré sur le débit — pour tester le montage
    "tts_engine": "edge",
    "voice": None,  # None = voix par défaut de la langue
    "tts_rate": "+0%",  # edge-tts uniquement
    "tts_concurrency": 4,
    "pause_between_segments": 0.25,  # secondes de silence entre segments

    # --- Visuels -----------------------------------------------------------
    # "slides" : le texte narré s'affiche en grand sur chaque diapositive
    # "card"   : carte fixe (titre) + sous-titres incrustés en bas d'écran
    # "illustre" : illustrations libres de droits (Wikimedia Commons) en plein écran,
    #              une par scène, avec zoom lent et texte dans un bandeau
    "visual_style": "slides",
    "scene_seconds": 40,  # durée visée d'une scène illustrée
    "ken_burns": True,  # zoom / travelling lent sur les illustrations (plus lent à monter)
    "zoom_supersampling": 1.5,  # 1 = plus rapide, 2 = mouvement plus fluide
    "illustration_theme": None,  # recherche de secours, ex. "veld Afrique du Sud"
    "illustration_hint": "",  # ajouté à chaque recherche, ex. "painting"
    "resolution": (1920, 1080),
    "background": ((22, 27, 45), (48, 36, 74)),  # dégradé haut -> bas
    "text_color": (245, 245, 245),
    "accent_color": (255, 196, 87),
    "font_path": None,  # None = police détectée automatiquement
    "background_image": None,  # image de fond optionnelle (chemin)

    # --- Vidéo -------------------------------------------------------------
    "fps": 25,
    "video_codec": "libx264",
    "crf": 23,
    # Vitesse d'encodage x264 : "veryfast" est ~40 % plus rapide que "medium" pour une
    # qualité visuelle quasi identique sur ce type de vidéo (images fixes).
    "preset": "veryfast",
    "audio_bitrate": "160k",

    # --- Publication YouTube -----------------------------------------------
    "client_secrets": BASE_DIR / "client_secrets.json",
    "token_file": BASE_DIR / "token.json",
    "privacy_status": "private",  # "private", "unlisted" ou "public"
    "category_id": "27",  # 27 = Éducation
    "tags": ["livre audio", "lecture"],
    "create_playlist": True,
    "upload_captions": True,
}
