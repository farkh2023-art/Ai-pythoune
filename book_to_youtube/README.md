# Livre → série de vidéos YouTube

Application 100 % Python qui transforme un livre (PDF, EPUB, DOCX, TXT) en
série d'épisodes vidéo prêts pour YouTube : narration synthétique, diapositives,
sous-titres SRT, miniatures — puis publication **uniquement après validation du
créateur de la chaîne**.

## Pipeline

| Étape | Module | Entrée → Sortie |
|---|---|---|
| 1. Lecture | `modules/reader.py` | PDF / EPUB / DOCX / TXT → texte brut + titre/auteur |
| 2. Nettoyage | `modules/cleaner.py` | en-têtes, pieds de page, numéros de page, césures, retours à la ligne → paragraphes |
| 3. Découpage | `modules/splitter.py` | texte → épisodes (chapitre, durée cible ou nombre de mots) |
| 4. Script | `modules/scripter.py` | épisode → segments à lire (intro, texte, outro) |
| — Résumé (optionnel) | `modules/summarizer.py` | épisode → narration résumée par Claude |
| 5. Voix off | `modules/tts.py` | segments → WAV + minutage exact de chaque segment |
| 6. Visuels | `modules/visuals.py` | une image par segment + miniature 1280×720 |
| — Sous-titres | `modules/subtitles.py` | minutage → fichier `.srt` |
| 7. Montage | `modules/video.py` | images + audio → MP4 H.264/AAC (ffmpeg) |
| 8. Publication | `modules/uploader.py` | relecture, validation, envoi via YouTube Data API v3 |

Chaque segment est synthétisé séparément : les diapositives et les sous-titres
sont donc parfaitement synchronisés avec la voix.

## Installation

```bash
cd book_to_youtube
python -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -r requirements.txt
```

**ffmpeg** (avec ffprobe) doit être installé et dans le `PATH` :
`apt install ffmpeg`, `brew install ffmpeg` ou `winget install ffmpeg`.

## Utilisation

### 1. Générer les vidéos

```bash
python main.py build data/input/mon_livre.pdf
```

Options utiles :

| Option | Effet |
|---|---|
| `--split chapter\|duration\|words` | règle de découpage (défaut : `chapter`, repli sur `duration` si aucun chapitre n'est détecté) |
| `--minutes 8` / `--words 1200` | taille cible d'un épisode |
| `--max-minutes 20` | en mode chapitre, un chapitre plus long est découpé en parties égales |
| `--lang fr\|en` | langue de la narration |
| `--narration fidele\|resume` | lecture intégrale (défaut) ou résumé de chaque épisode — voir ci-dessous |
| `--summary-minutes 5` | durée visée d'un épisode résumé |
| `--tts edge\|gtts\|pyttsx3\|silent` | moteur de synthèse vocale (`silent` = test du montage sans voix) |
| `--voice fr-FR-HenriNeural` `--rate +10%` | voix et débit (edge-tts : `edge-tts --list-voices`) |
| `--style slides\|card` | texte narré en grand, ou carte de titre + sous-titres incrustés |
| `--background image.jpg` | image de fond (floutée et assombrie automatiquement) |
| `--resolution 1280x720` | résolution (défaut 1920×1080) |
| `--episodes 1,3,5-7` / `--max-episodes 2` | ne générer qu'une partie de la série |
| `--force` | régénérer des épisodes déjà produits |

Les résultats sont dans `data/output/<titre-du-livre>/` :
`episode_001.mp4`, `episode_001.srt`, `episode_001.jpg` et `manifest.json`.
Les textes découpés sont dans `data/episodes/` pour relecture. La synthèse vocale
est mise en cache : une génération interrompue reprend là où elle s'était arrêtée.

### Mode résumé

Avec `--narration resume`, chaque épisode est résumé par Claude (API Anthropic)
avant la synthèse vocale : un chapitre de 30 minutes devient par exemple un
épisode d'environ 5 minutes (`--summary-minutes`). Le résumé est fidèle au
texte, rédigé pour être écouté (prose continue, sans titres ni listes), et la
description YouTube précise qu'il a été rédigé avec l'aide d'une IA.

```bash
export ANTHROPIC_API_KEY=sk-ant-...        # clé API : https://console.anthropic.com/
python main.py build data/input/mon_livre.epub --narration resume --summary-minutes 6
```

Les résumés sont enregistrés dans `data/episodes/<livre>/episode_001.resume.<id>.txt`.
Vous pouvez les relire et les corriger : relancez ensuite la génération avec
`--force` pour refaire la vidéo à partir de votre version (aucun nouvel appel à
l'API tant que le texte du chapitre et les réglages ne changent pas). Modèle et
niveau d'effort se règlent dans `config.py` (`summary_model`, `summary_effort`).

### 2. Validation par le créateur de la chaîne

Toute vidéo générée est au statut **`a_valider`**. Rien n'est publié tant que le
créateur ne l'a pas approuvée.

```bash
python main.py status  data/output/mon-livre/manifest.json   # état de la série
python main.py review  data/output/mon-livre/manifest.json   # relecture interactive
python main.py approve data/output/mon-livre/manifest.json --episodes 1-3
python main.py reject  data/output/mon-livre/manifest.json --episodes 4 --note "voix trop rapide"
```

Titres, descriptions et tags peuvent être corrigés directement dans
`manifest.json` avant l'envoi. Une vidéo régénérée (`--force`) repasse
automatiquement `a_valider`.

### 3. Publication YouTube

1. Dans [Google Cloud Console](https://console.cloud.google.com/), activer
   **YouTube Data API v3** et créer un identifiant OAuth de type
   « Application de bureau ».
2. Télécharger le JSON sous `book_to_youtube/client_secrets.json`.
3. Lancer :

```bash
python main.py upload data/output/mon-livre/manifest.json            # tous les épisodes validés
python main.py upload data/output/mon-livre/manifest.json --privacy unlisted
```

Au premier lancement, un navigateur s'ouvre pour autoriser la chaîne (jeton
enregistré dans `token.json`). Seuls les épisodes `valide` sont envoyés ; ils
sont mis en ligne en **privé** par défaut, ajoutés à une playlist de la série,
avec miniature et sous-titres. Demander explicitement un épisode non validé
(`--episodes 4`) provoque une erreur.

Quota : l'API accorde 10 000 unités/jour par défaut et un envoi coûte environ
1 600 unités — comptez ~6 vidéos par jour. La miniature personnalisée exige une
chaîne vérifiée (sinon un simple avertissement est affiché).

## Configuration

Toutes les valeurs par défaut sont dans `config.py` (dossiers, découpage,
débit de narration, voix, couleurs, police, encodage vidéo, confidentialité,
catégorie YouTube…).

## Tests

```bash
python -m pytest -q tests
```

Les tests couvrent la lecture des 4 formats, le nettoyage, le découpage, le
script, le mode résumé (client Claude simulé), un montage complet (moteur
`silent`) et le circuit de validation / publication (client YouTube simulé).

## Remarques

- **Droits d'auteur** : ne publiez que des livres du domaine public ou dont vous
  détenez les droits d'adaptation audiovisuelle.
- La description indique que la narration est une voix de synthèse.
- `edge-tts` et `gTTS` nécessitent une connexion Internet ; `pyttsx3` fonctionne
  hors-ligne (installer `espeak-ng` sous Linux).
- Le mode résumé est facturé à l'usage par l'API Anthropic (un appel par épisode,
  dont le texte du chapitre en entrée).
