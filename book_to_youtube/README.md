# Livre → série de vidéos YouTube

Application 100 % Python qui transforme un livre (PDF, EPUB, DOCX, TXT) en
série d'épisodes vidéo prêts pour YouTube : narration synthétique, diapositives,
sous-titres SRT, miniatures — puis publication **uniquement après validation du
créateur de la chaîne**.

## Pipeline

| Étape | Module | Entrée → Sortie |
|---|---|---|
| 1. Lecture | `modules/reader.py` | PDF / EPUB / DOCX / TXT → texte brut + titre/auteur |
| — OCR (PDF scanné) | `modules/ocr.py` | images des pages → texte (Claude ou Tesseract) |
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
pip install pypdf edge-tts ebooklib python-docx google-api-python-client google-auth-oauthlib arabic-reshaper
```

PyMuPDF n'a pas de paquet précompilé pour Android : sans lui, les PDF sont lus
avec **pypdf** (pur Python, plus lent mais équivalent pour ce projet). Conseils :
`--resolution 1280x720` pour un montage plus rapide, `termux-wake-lock` pour ne
pas être interrompu écran éteint, et `cp ~/storage/downloads/livre.pdf data/input/`
pour récupérer un livre téléchargé.

Fonctions Claude (résumé, présentation, prompts d'images, OCR) : inutile
d'installer le paquet `anthropic` sous Termux (sa dépendance pydantic-core se
compile mal) ; sans lui, l'application appelle l'API directement en HTTP. Il
suffit de définir la clé, par exemple dans `~/.bashrc` :
`export ANTHROPIC_API_KEY=sk-ant-…`.

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
- **supprimer une série** (bouton en bas de sa carte, après confirmation) : vidéos,
  textes, voix, images et prompts sont effacés, et au choix le livre importé ; ce qui
  est déjà sur YouTube ou copié dans Téléchargements n'est pas touché.

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

Titres des épisodes : un livre sans chapitres détectés (découpage par durée ou
par mots) donne des épisodes « Partie 1 », « Partie 2 »… Avec la clé Claude,
chacun reçoit un vrai titre court et évocateur (ex. « Le serment du sommet »),
affiché dans le générique et repris sur YouTube. Les titres sont gardés dans
`data/episodes/<livre>/titres.json`, modifiable à la main (régénérez ensuite
l'épisode) ; `episode_titles = "off"` dans `config.py` pour s'en passer.

### Livres scannés (OCR)

Un PDF scanné (photos ou captures d'écran des pages, sans texte sélectionnable)
est reconnu automatiquement : ses pages passent par la **reconnaissance de
texte (OCR)** avant le découpage, sans rien changer à la commande ni au tableau
de bord (étape « Reconnaissance du texte (OCR) »).

- **Avec la clé Claude** (`export ANTHROPIC_API_KEY=…`) : Claude lit les images
  des pages, 4 par requête. Meilleure qualité, même sur des captures d'écran de
  téléphone : barre d'état, boutons et numéros de page sont ignorés, les titres de
  chapitre restent sur leur ligne.
- **Sans clé, hors ligne : Tesseract.**
  ```bash
  # Termux
  pkg install tesseract
  curl -L -o $PREFIX/share/tessdata/fra.traineddata \
       https://github.com/tesseract-ocr/tessdata_fast/raw/main/fra.traineddata
  # Debian / Ubuntu : sudo apt install tesseract-ocr tesseract-ocr-fra
  ```

Le texte reconnu est mis en cache page par page dans `data/ocr/` : une
génération interrompue ne relit pas les pages déjà traitées. Réglages dans
`config.py` : `ocr_engine` (`auto`, `claude`, `tesseract`, `off`),
`ocr_batch_pages`, `ocr_effort`. Pour relire ou corriger le texte, voir
`data/episodes/<livre>/` après le découpage.

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

   Chaque épisode est découpé en scènes (voir « Découpage visuel » ci-dessous). Le dossier
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

**Découpage visuel par Claude.** Avec une clé API, Claude découpe chaque épisode
selon ce qu'on doit voir : une nouvelle scène commence quand le lieu, le personnage,
l'objet ou l'étape change (de 8 à 30 s environ, `scene_min_seconds` /
`scene_max_seconds` dans `config.py`), et chaque prompt illustre exactement les phrases
lues pendant son image (`Texte lu` dans `prompts.txt`). Sans clé, les scènes durent
environ 40 s (`--scene-seconds`). Le découpage est gardé dans `decoupage.json` : il ne
bouge plus ensuite. Un épisode dont vous avez déjà déposé des images garde son ancien
découpage ; `--scene-cut claude` (ou « Visuel par Claude, même si des images existent »
dans le tableau de bord) le redécoupe, et il faut alors refaire ses images.
`--scene-cut duree` revient au découpage fixe.

### Scènes animées (option-anime)

Deux options pour les visuels des scènes :

- **option-images** (`--visuels images`, par défaut) : une image fixe par scène, avec
  zoom lent ou non ;
- **option-anime** (`--visuels anime`) : chaque scène peut recevoir un **clip animé** que
  vous générez vous-même, plus tard, dans un générateur image→vidéo (Kling, Runway, Veo,
  Hailuo, Luma…). L'application ne génère pas les animations : elle prépare les prompts et
  monte vos clips.

Les scènes sont les mêmes dans les deux options : on peut commencer avec des images, puis
animer les scènes une à une. En option-anime :

1. `--prompts-only` (ou **Préparer les images…**) écrit en plus, dans le dossier de
   chaque épisode, `prompts_animation.txt` : un prompt de mouvement par scène (caméra,
   mouvement du sujet, ambiance, ~5 s), rédigé par Claude à partir du texte lu et du
   prompt de l'image ;
2. créez l'image de la scène (`prompts.txt`), puis animez-la avec ce prompt ;
3. déposez le clip sous le nom `scene_01.mp4` (MP4, MOV, WEBM ou MKV) à côté de
   `scene_01.jpg`, ou utilisez **Ajouter un clip** dans la fenêtre **Images** du
   tableau de bord ;
4. générez avec `--visuels anime`.

Chaque clip est recadré en 16:9, sa bande-son est ignorée, et s'il est plus court que sa
scène il est rejoué en **aller-retour** (sans saut visible, par défaut), en **boucle**,
ou **figé** sur sa dernière image (`--anime-fill`). Une scène sans clip garde son image,
avec ou sans zoom.

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

### « Savez-vous pourquoi… ??? » : Shorts et vidéos de curiosité

Un **mot-clé** au lieu d'un livre. Claude écrit 4 questions, une par angle :
**pourquoi**, **le saviez-vous**, **comment**, **qu'est-ce que**. Chacune a son script
d'environ 45 s (la question en accroche, la réponse, un détail surprenant, une invitation à
commenter), un titre YouTube, 3 hashtags et **ce qu'il faut vérifier** avant de publier.

```bash
python main.py curiosite "sommeil" --domaine "santé et corps humain" --lang fr
# → data/input/sommeil-fr.curiosite.json (à relire et corriger)
python main.py build data/input/sommeil-fr.curiosite.json --format short --style illustre
python main.py build data/input/sommeil-fr.curiosite.json --split single --style illustre
```

- `--format short` : **4 Shorts verticaux 9:16** (1080×1920, ou 720×1280 avec
  `--resolution 1280x720`), un par question, sans générique (la question s'affiche en
  haut dès la première seconde), sous-titres en grand dans la zone visible, fin « Abonnez-vous ».
  Titre YouTube avec `#Shorts`, hashtags dans la description, série distincte « …-short ».
  Les prompts d'images demandent des images **portrait 9:16**.
- `--split single` : **une vidéo longue 16:9** qui enchaîne les 4 questions, avec le
  générique de la chaîne.
- Version arabe : `--lang ar` sur le même fichier (Claude traduit questions et scripts), ou
  `curiosite "النوم" --lang ar` pour des questions écrites directement en arabe.

Dans le tableau de bord : carte **Savez-vous pourquoi… ???** (mot-clé, domaine, langue),
puis **Questions…** sur le fichier pour relire et corriger, puis **Générer…** avec
« Format : Short » ou « Découpage : Une seule vidéo ».

**Exactitude.** Claude doit s'en tenir aux faits établis, présenter les légendes comme
telles et ne pas donner de conseil médical, mais **relisez toujours** : la ligne
« À vérifier » de chaque question dit quoi contrôler et où. Un exemple relu est fourni :
`exemples/sommeil-fr.curiosite.json` (copiez-le dans `data/input/`).

### Langues (multilingue)

La vidéo peut être en **français, anglais, arabe, espagnol, allemand, italien ou
portugais** (`--lang fr|en|ar|es|de|it|pt`, ou « Langue de la vidéo » dans le tableau de
bord). La langue règle la voix, le générique (« Bienvenue sur… », « الحلقة 1 »…), les
titres et la description YouTube, et les textes rédigés par Claude (présentation,
titres, résumé).

- **Livre et vidéo dans la même langue** : le livre est lu tel quel (un livre arabe
  donne une vidéo arabe).
- **Langues différentes** (ex. livre français, vidéo arabe) : avec la clé Claude, le
  livre est **traduit** fidèlement, chapitre par chapitre ; le titre du livre et des
  chapitres aussi. En mode résumé, Claude résume directement dans la langue de la vidéo.
  La version traduite est une **série à part** (dossier `…-ar`), à côté de l'originale ;
  les traductions sont gardées dans `data/episodes/<série>/` et ne sont jamais refaites.
- La langue du livre est détectée automatiquement ; `--book-lang` la fixe si besoin.

Voix edge-tts proposées (choix « Voix » du tableau de bord, ou `--voice`) : pour l'arabe,
`ar-SA-HamedNeural` (par défaut), `ar-MA-JamalNeural` / `ar-MA-MounaNeural` (Maroc),
`ar-EG-…` (Égypte)…

**Écriture arabe** (de droite à gauche, lettres liées) : gérée par Pillow s'il inclut
raqm ; sinon (souvent sous Termux) installez `pip install arabic-reshaper`. Il faut aussi
une police arabe : `pkg install ttf-dejavu` sous Termux (sinon Noto Naskh Arabic du
téléphone est utilisée). OCR d'un livre arabe scanné avec Tesseract : fichier de langue
`ara.traineddata` (Claude n'en a pas besoin).

Ajouter une langue : une entrée dans `modules/languages.py` (libellés, phrases du
générique, voix).

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
