"""Configuration centrale du pipeline livre -> vidéos YouTube.

Toutes les valeurs peuvent être surchargées depuis la ligne de commande
(voir ``main.py --help``) ou en modifiant ce dictionnaire.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
# AIP_DATA_DIR permet d'utiliser un autre dossier de travail (tests, plusieurs projets…).
DATA_DIR = Path(os.environ.get("AIP_DATA_DIR") or BASE_DIR / "data")

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
    # Titres des épisodes sans chapitre (« Partie N ») proposés par Claude :
    # "auto" (si une clé est configurée) ou "off".
    "episode_titles": "auto",
    # --- OCR des PDF scannés (pages en images) -------------------------------
    # "auto" : Claude si une clé est configurée, sinon Tesseract s'il est installé ;
    # "claude", "tesseract" ou "off".
    "ocr_engine": "auto",
    "ocr_batch_pages": 4,  # pages envoyées à Claude par requête
    "ocr_effort": "low",
    "add_intro": True,
    # Générique : la présentation du livre (data/episodes/<livre>/presentation.txt)
    # est lue au début de chaque épisode ; False = seulement affichée.
    "read_presentation": True,
    # Sans presentation.txt, Claude la rédige automatiquement (si une clé API est configurée).
    "auto_presentation": True,
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
    "scene_seconds": 40,  # durée visée d'une scène illustrée (découpage par durée)
    # Découpage des scènes : "auto" = visuel par Claude si une clé est configurée
    # (nouvelle scène quand ce qu'on voit change), "claude" = forcé, "duree" = fixe.
    "scene_cut": "auto",
    "scene_min_seconds": 8,  # bornes visées du découpage visuel
    "scene_max_seconds": 30,
    "ken_burns": True,  # zoom / travelling lent sur les illustrations (plus lent à monter)
    "zoom_supersampling": 1.5,  # 1 = plus rapide, 2 = mouvement plus fluide
    "illustration_theme": None,  # recherche de secours, ex. "veld Afrique du Sud"
    "illustration_hint": "",  # ajouté à chaque recherche, ex. "painting"
    # Source des images : vos images (data/illustrations/<livre>/episode_NNN/scene_01.jpg…)
    # puis Wikimedia Commons pour les scènes sans image ("dossier", "commons" possibles).
    "illustration_source": "dossier+commons",
    # Prompts d'images : "auto" = Claude si une clé API est configurée, sinon modèle simple.
    "image_prompts": "auto",
    "image_style": None,  # style imposé dans les prompts (défaut : peinture numérique)
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
