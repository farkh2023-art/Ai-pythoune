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

### Android (Termux)

```bash
pkg update && pkg upgrade
pkg install python ffmpeg git python-pillow libxml2 libxslt
termux-setup-storage
git clone https://github.com/farkh2023-art/Ai-pythoune.git && cd Ai-pythoune/book_to_youtube
pip install pypdf edge-tts ebooklib python-docx google-api-python-client google-auth-oauthlib
```

PyMuPDF n'a pas de paquet précompilé pour Android : sans lui, les PDF sont lus
avec **pypdf** (pur Python, plus lent mais équivalent pour ce projet). Conseils :
`--resolution 1280x720` pour un montage plus rapide, `termux-wake-lock` pour ne
pas être interrompu écran éteint, et `cp ~/storage/downloads/livre.pdf data/input/`
pour récupérer un livre téléchargé.

## Tableau de bord

Tout le parcours (import du livre → génération → validation → publication) peut
se piloter depuis un navigateur, y compris sur le téléphone :

```bash
python dashboard.py            # Termux : ajoutez --open pour ouvrir Chrome directement
```

Ouvrez l'adresse affichée (`http://127.0.0.1:8765/#token=…`). Le tableau de bord
permet de :

- **importer un livre** depuis le téléphone (plus besoin de `cp`) ;
- régler **votre chaîne** (logo, nom affiché et prononcé, slogan) et la **présentation**
  de chaque livre, pour le générique ;
- **préparer les images** : prompts par scène, à copier, et ajout de vos images
  depuis la galerie, scène par scène ;
- **lancer la génération** avec un formulaire (découpage, voix, style, illustrations…) ;
- **suivre la tâche en cours** : épisode, étape (voix, illustrations, montage…), journal
  en direct, annulation ;
- voir chaque série en **5 étapes** (import, découpage, génération, validation,
  publication) et chaque épisode avec son statut (à générer, en cours, à valider,
  validé, rejeté, publié) ;
- **regarder un épisode**, corriger son titre et sa description, le valider ou le rejeter,
  le régénérer avec les mêmes réglages ;
- **publier** les épisodes validés (privé, non répertorié ou public), avec le lien
  d'autorisation Google cliquable dans le journal au premier envoi.

Le serveur n'écoute que sur l'appareil lui-même, et chaque requête doit porter le
jeton secret de l'adresse affichée : ne la partagez pas. Une seule tâche tourne à la
fois. Les commandes en ligne (`build`, `review`, `upload`…) restent utilisables en
parallèle du tableau de bord.

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
| `--style slides\|card\|illustre` | texte narré en grand, carte de titre + sous-titres, ou illustrations en plein écran (voir ci-dessous) |
| `--background image.jpg` | image de fond (floutée et assombrie automatiquement) |
| `--resolution 1280x720` | résolution (défaut 1920×1080) |
| `--episodes 1,3,5-7` / `--max-episodes 2` | ne générer qu'une partie de la série |
| `--force` | régénérer des épisodes déjà produits |

Les résultats sont dans `data/output/<titre-du-livre>/` :
`episode_001.mp4`, `episode_001.srt`, `episode_001.jpg` et `manifest.json`.
Les textes découpés sont dans `data/episodes/` pour relecture. La synthèse vocale
est mise en cache : une génération interrompue reprend là où elle s'était arrêtée.

### Générique de la chaîne

Chaque épisode commence par le même générique, où seuls le numéro et le titre de
l'épisode changent :

> « Bienvenue sur Le Grenier du Savoir. *Le Collier du prêtre Jean*, de John Buchan.
> *(présentation du livre)* Épisode 3 : Blaauwildebeestefontein. »

L'image affichée pendant ce temps montre le logo, le nom et le slogan de la chaîne,
le livre, sa présentation, puis le numéro et le titre de l'épisode. Le petit logo
apparaît aussi en haut de toutes les images et sur la miniature YouTube.

À régler une fois dans le tableau de bord (section **Ma chaîne**), ou à la main :

- `data/chaine/chaine.json` : `{"nom": "Le GrenieR du SavoiR", "nom_lu": "Le Grenier du
  Savoir", "slogan": "Le Savoir de demain"}`. `nom_lu` est le nom tel que la voix doit le
  prononcer, sans tirets bas ni majuscules au milieu des mots.
- `data/chaine/logo.png` (ou `.jpg` / `.webp`) : de préférence le fichier d'origine, carré.
- `data/episodes/<livre>/presentation.txt` : la présentation du livre, 3 à 5 phrases.
  Elle est lue au début de chaque épisode ; avec `read_presentation: False` dans
  `config.py`, elle est seulement affichée.

**Présentation rédigée par Claude.** Avec une clé API Anthropic
(`export ANTHROPIC_API_KEY=…`), si le livre n'a pas encore de présentation, Claude la
rédige automatiquement lors de « Préparer les images » ou de la première génération :
3 à 5 phrases sur l'époque, le lieu, le héros et l'enjeu, **sans dévoiler la fin**. Elle
est enregistrée dans `presentation.txt` et reste modifiable ; une présentation existante
n'est jamais remplacée automatiquement (`auto_presentation: False` pour désactiver).

- Dans le tableau de bord, **Proposer avec Claude** affiche une nouvelle proposition
  dans le champ : relisez-la, puis **Enregistrer**.
- En ligne de commande : `python main.py presentation data/input/livre.pdf`
  (`--force` pour remplacer la présentation existante).

### Vos propres images, scène par scène

1. **Préparer les prompts** dès l'import du livre, sans voix ni vidéo :

   ```bash
   python main.py build data/input/livre.pdf --style illustre --prompts-only
   ```

   Chaque épisode est découpé en scènes d'environ 40 s. Le dossier
   `data/illustrations/<livre>/episode_001/` reçoit un `prompts.txt` (un prompt par
   scène) et `PROMPTS.md` les rassemble tous. Avec une clé API Anthropic, Claude
   rédige les prompts en anglais, d'après une « bible visuelle » du livre (époque,
   lieux, apparence des personnages) pour garder des images cohérentes ; sans clé, le
   prompt est construit à partir du texte de la scène. `--image-style "aquarelle"`
   impose un style à toutes les images.
2. **Créer les images** avec votre générateur (ChatGPT, Gemini, Midjourney…), au format
   paysage 16:9.
3. **Les déposer** dans le dossier de l'épisode sous les noms `scene_01.jpg`,
   `scene_02.png`… Le plus simple est le bouton **Images** de chaque épisode dans le
   tableau de bord, qui affiche le prompt, le copie, et ajoute l'image depuis la galerie.
4. **Générer** avec les **mêmes réglages de découpage** :
   `python main.py build data/input/livre.pdf --style illustre`.

Les scènes sans image prennent une image de Wikimedia Commons (`--images dossier` pour
n'utiliser que les vôtres ; une scène sans image reprend alors la précédente). Vos images
n'ont pas de crédit dans la description. Les scènes sont calculées sur le texte seul :
modifier la présentation ou le générique ne décale pas vos images. En revanche, changer
le découpage (`--split`, `--minutes`, `--scene-seconds`, `--narration`) change les
scènes : les prompts sont alors réécrits.

### Style illustré (images libres de droits)

Avec `--style illustre`, chaque épisode est découpé en scènes d'environ 40 s
(`--scene-seconds`). Pour chaque scène, les mots les plus caractéristiques du
texte servent à chercher une image sur **Wikimedia Commons**. Elle s'affiche en
plein écran avec un zoom ou un travelling lent (`--no-zoom` pour des images
fixes), et le texte lu apparaît dans un bandeau en bas.

```bash
python main.py build data/input/livre.pdf --style illustre --theme "veld Afrique du Sud" --image-hint painting
```

- Seules les images sous licence libre sont retenues (domaine public, CC0,
  CC BY, CC BY-SA). Leurs crédits (titre, auteur, licence, lien) sont ajoutés
  automatiquement à la description YouTube, comme l'exigent les licences CC BY.
- `--theme` donne une recherche de secours quand une scène ne donne rien, et
  `--image-hint` ajoute un mot à chaque recherche (« painting » donne souvent
  des tableaux anciens, plus cohérents pour un roman).
- La recherche se fait par mots-clés : les images illustrent l'ambiance (mer,
  forêt, navire…), pas la scène exacte. Les descriptions de Commons étant
  surtout en anglais, `--theme` et `--image-hint` en anglais donnent souvent de
  meilleurs résultats.
- **Regardez chaque épisode avant de le valider** : Commons n'a pas de filtre
  de contenu, et une image peut être hors sujet ou inadaptée à votre chaîne.
- Les images et les recherches sont gardées dans `data/images/commons/` :
  relancer une génération ne retélécharge rien.

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

### Envoi manuel (application YouTube)

Les vidéos envoyées par l'API depuis un projet Google non audité restent **privées**.
Pour publier tout de suite, utilisez le bouton **Envoi manuel** d'un épisode validé
dans le tableau de bord :

1. la vidéo, la miniature, les sous-titres et un fichier `titre_description.txt` sont
   copiés dans **Téléchargements/AI-pythoune/<livre>/episode_NNN/** ;
2. copiez le titre et la description avec les boutons prévus ;
3. dans l'application YouTube, *+ → Importer une vidéo*, choisissez `video.mp4`, puis
   ajoutez dans YouTube Studio la miniature, les sous-titres et la playlist ;
4. collez le lien de la vidéo publiée : l'épisode passe en **Publié** et n'est plus
   proposé à l'envoi automatique.

## Configuration

Toutes les valeurs par défaut sont dans `config.py` (dossiers, découpage,
débit de narration, voix, couleurs, police, encodage vidéo, confidentialité,
catégorie YouTube…).

## Tests

```bash
python -m pytest -q tests
```

Les tests couvrent la lecture des 4 formats, le nettoyage, le découpage, le
script, le mode résumé (client Claude simulé), les illustrations (API Commons
simulée), un montage complet (moteur `silent`), le circuit de validation /
publication (client YouTube simulé) et l'API du tableau de bord.

## Remarques

- **Droits d'auteur** : ne publiez que des livres du domaine public ou dont vous
  détenez les droits d'adaptation audiovisuelle.
- La description indique que la narration est une voix de synthèse.
- `edge-tts` et `gTTS` nécessitent une connexion Internet ; `pyttsx3` fonctionne
  hors-ligne (installer `espeak-ng` sous Linux).
- Le mode résumé est facturé à l'usage par l'API Anthropic (un appel par épisode,
  dont le texte du chapitre en entrée).
