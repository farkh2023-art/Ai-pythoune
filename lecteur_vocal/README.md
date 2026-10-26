# Lecteur vocal — écoutez vos PDF, avec votre propre voix

Web app en un seul fichier (`index.html`), pensée pour le téléphone :

- **Écouter un PDF** (ou un texte collé) : le texte est extrait sur l'appareil
  (pdf.js), lu phrase par phrase et surligné mot à mot.
- **Barre d'outils** : accueil, liste des pages, mode concentration (œil),
  surlignage (CC), taille du texte, recherche, menu.
- **Lecteur** : choix de la voix, phrase précédente / suivante, lecture / pause,
  vitesse de 0,75× à 2,5×. Touchez une phrase pour reprendre la lecture à cet endroit.
- **Cloner ma voix** : enregistrez 50 à 90 secondes (ou importez un fichier
  audio), cochez la confirmation que c'est bien votre voix, puis « Créer la voix ».

## Lancer

Le micro n'est accessible qu'en contexte sécurisé : servez le dossier en local
plutôt que d'ouvrir le fichier directement.

```bash
cd ~/Ai-pythoune && git pull      # récupère la dernière version
python lecteur_vocal/serve.py --open
```

Puis ouvrez <http://127.0.0.1:8000> (`--open` le fait pour vous, y compris
sous Termux). `serve.py` sert **toujours son propre dossier**, quel que soit le
dossier courant, et n'écoute que sur cet appareil. Évitez `python -m http.server`
lancé depuis le mauvais dossier : il publierait tout ce dossier (par exemple
`~/.ssh` ou `~/.git-credentials`) sur le réseau local.
Une connexion Internet est nécessaire au premier chargement (pdf.js et polices).

## Voix

| Voix | Fonctionnement | Clé requise |
|---|---|---|
| Voix de l'appareil | Synthèse vocale du navigateur (Web Speech API), hors ligne si la voix est locale | non |
| Voix clonée | API ElevenLabs : clonage instantané (`/v1/voices/add`) puis lecture (`/v1/text-to-speech/{id}/with-timestamps`) | oui |

La clé API se saisit dans le menu ⋯ → « Clé API ». Elle est stockée dans le
`localStorage` du navigateur, sur cet appareil uniquement — n'utilisez pas l'app
sur un appareil partagé avec votre clé. La lecture avec une voix clonée consomme
des crédits ElevenLabs (une requête par phrase, la suivante est préchargée).

Le clonage est réservé à **votre propre voix** : l'app demande une confirmation
explicite et le service vérifie lui-même l'échantillon.

## Limites

- Les PDF scannés (images) n'ont pas de texte : passez-les d'abord à l'OCR
  (voir `book_to_youtube/modules/ocr.py`).
- La détection des paragraphes repose sur l'interligne : les mises en page
  en colonnes peuvent être lues dans un ordre imparfait.
