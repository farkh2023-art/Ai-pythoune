"""Langues prises en charge : textes du générique, libellés, voix, OCR, écriture.

Une langue = la langue de la **vidéo** (narration, générique, sous-titres,
description YouTube). Le livre peut être dans une autre langue : il est alors
traduit par Claude (voir ``translator.py``).

Pour ajouter une langue, il suffit d'ajouter une entrée à ``LANGUAGES``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Language:
    code: str
    name: str        # nom en français, utilisé dans les consignes données à Claude
    native: str      # nom dans la langue elle-même (menus)
    voices: tuple[tuple[str, str], ...]  # voix edge-tts : (identifiant, description)
    labels: dict = field(default_factory=dict)
    script: dict = field(default_factory=dict)  # phrases lues du générique
    tesseract: str = "eng"
    rtl: bool = False  # écriture de droite à gauche (arabe…)
    stopwords: frozenset = frozenset()  # mots fréquents, pour reconnaître la langue

    @property
    def default_voice(self) -> str:
        return self.voices[0][0]


def _words(text: str) -> frozenset:
    return frozenset(text.split())


LANGUAGES: dict[str, Language] = {
    "fr": Language(
        "fr", "français", "Français",
        (("fr-FR-HenriNeural", "Henri (homme)"), ("fr-FR-DeniseNeural", "Denise (femme)"),
         ("fr-FR-EloiseNeural", "Eloise (femme)"), ("fr-FR-RemyMultilingualNeural", "Rémy (homme)"),
         ("fr-CA-AntoineNeural", "Antoine (homme, Canada)"),
         ("fr-BE-GerardNeural", "Gérard (homme, Belgique)")),
        labels={"question": "Question", "subscribe": "Abonnez-vous pour d'autres questions !",
                "episode": "Épisode", "next": "À suivre…", "end": "Fin", "part": "Partie",
                "by": "de", "voice": "Narration : voix de synthèse.",
                "summary": "Résumé du texte original, rédigé avec l'aide d'une IA.",
                "translation": "Traduction du texte original réalisée avec l'aide d'une IA.",
                "images": "Illustrations (Wikimedia Commons, licences libres) :"},
        script={
            "outro_short": "Abonnez-vous pour découvrir d'autres questions.",
            "welcome": "Bienvenue sur {channel}.",
            "book": "{book}.",
            "book_author": "{book}, de {author}.",
            "episode": "Épisode {number} : {title}.",
            "outro": "Fin de l'épisode {number}. Merci de votre écoute, à bientôt pour la suite.",
            "outro_last": "Fin de l'épisode {number}, et fin du livre. Merci de nous avoir suivis.",
        },
        tesseract="fra",
        stopwords=_words("le la les des et est une que dans pour qui pas sur avec il elle "
                         "nous vous ce cette mais ou du au aux"),
    ),
    "en": Language(
        "en", "anglais", "English",
        (("en-US-GuyNeural", "Guy (man, US)"), ("en-US-AriaNeural", "Aria (woman, US)"),
         ("en-GB-RyanNeural", "Ryan (man, UK)"), ("en-GB-SoniaNeural", "Sonia (woman, UK)")),
        labels={"question": "Question", "subscribe": "Subscribe for more questions!",
                "episode": "Episode", "next": "To be continued…", "end": "The End",
                "part": "Part", "by": "by", "voice": "Narration: synthetic voice.",
                "summary": "Summary of the original text, written with AI assistance.",
                "translation": "Translation of the original text, made with AI assistance.",
                "images": "Illustrations (Wikimedia Commons, free licenses):"},
        script={
            "outro_short": "Subscribe to discover more questions.",
            "welcome": "Welcome to {channel}.",
            "book": "{book}.",
            "book_author": "{book}, by {author}.",
            "episode": "Episode {number}: {title}.",
            "outro": "End of episode {number}. Thanks for listening, see you in the next one.",
            "outro_last": "End of episode {number}, and the end of the book. Thanks for listening.",
        },
        tesseract="eng",
        stopwords=_words("the and of to is that was for with his her it they this have from "
                         "not but are were"),
    ),
    "ar": Language(
        "ar", "arabe (arabe standard moderne)", "العربية",
        (("ar-SA-HamedNeural", "حامد (رجل، السعودية)"), ("ar-SA-ZariyahNeural", "زارية (امرأة، السعودية)"),
         ("ar-MA-JamalNeural", "جمال (رجل، المغرب)"), ("ar-MA-MounaNeural", "منى (امرأة، المغرب)"),
         ("ar-EG-ShakirNeural", "شاكر (رجل، مصر)"), ("ar-EG-SalmaNeural", "سلمى (امرأة، مصر)")),
        labels={"question": "السؤال", "subscribe": "اشترك لمزيد من الأسئلة!",
                "episode": "الحلقة", "next": "يتبع…", "end": "النهاية", "part": "الجزء",
                "by": "تأليف", "voice": "السرد: صوت اصطناعي.",
                "summary": "ملخّص للنص الأصلي، كُتب بمساعدة الذكاء الاصطناعي.",
                "translation": "ترجمة للنص الأصلي أُنجزت بمساعدة الذكاء الاصطناعي.",
                "images": "الرسوم (ويكيميديا كومنز، تراخيص حرة):"},
        script={
            "outro_short": "اشترك في القناة لتكتشف المزيد من الأسئلة.",
            "welcome": "مرحبًا بكم في {channel}.",
            "book": "{book}.",
            "book_author": "{book}، تأليف {author}.",
            "episode": "الحلقة {number}: {title}.",
            "outro": "انتهت الحلقة {number}. شكرًا على الاستماع، ونلتقي في الحلقة القادمة.",
            "outro_last": "انتهت الحلقة {number}، وانتهى الكتاب. شكرًا لمتابعتكم.",
        },
        tesseract="ara",
        rtl=True,
    ),
    "es": Language(
        "es", "espagnol", "Español",
        (("es-ES-AlvaroNeural", "Álvaro (hombre, España)"), ("es-ES-ElviraNeural", "Elvira (mujer, España)"),
         ("es-MX-JorgeNeural", "Jorge (hombre, México)"), ("es-MX-DaliaNeural", "Dalia (mujer, México)")),
        labels={"question": "Pregunta", "subscribe": "¡Suscríbete para más preguntas!",
                "episode": "Episodio", "next": "Continuará…", "end": "Fin", "part": "Parte",
                "by": "de", "voice": "Narración: voz sintética.",
                "summary": "Resumen del texto original, redactado con ayuda de una IA.",
                "translation": "Traducción del texto original, realizada con ayuda de una IA.",
                "images": "Ilustraciones (Wikimedia Commons, licencias libres):"},
        script={
            "outro_short": "Suscríbete para descubrir más preguntas.",
            "welcome": "Bienvenidos a {channel}.",
            "book": "{book}.",
            "book_author": "{book}, de {author}.",
            "episode": "Episodio {number}: {title}.",
            "outro": "Fin del episodio {number}. Gracias por escuchar, hasta el próximo.",
            "outro_last": "Fin del episodio {number} y fin del libro. Gracias por acompañarnos.",
        },
        tesseract="spa",
        stopwords=_words("el los las del por una con para está pero como más fue sus muy "
                         "también"),
    ),
    "de": Language(
        "de", "allemand", "Deutsch",
        (("de-DE-ConradNeural", "Conrad (Mann)"), ("de-DE-KatjaNeural", "Katja (Frau)"),
         ("de-DE-FlorianMultilingualNeural", "Florian (Mann)")),
        labels={"question": "Frage", "subscribe": "Abonniere für weitere Fragen!",
                "episode": "Folge", "next": "Fortsetzung folgt…", "end": "Ende", "part": "Teil",
                "by": "von", "voice": "Erzählung: synthetische Stimme.",
                "summary": "Zusammenfassung des Originaltextes, mit Hilfe einer KI verfasst.",
                "translation": "Übersetzung des Originaltextes, mit Hilfe einer KI erstellt.",
                "images": "Illustrationen (Wikimedia Commons, freie Lizenzen):"},
        script={
            "outro_short": "Abonniere den Kanal für weitere Fragen.",
            "welcome": "Willkommen bei {channel}.",
            "book": "{book}.",
            "book_author": "{book}, von {author}.",
            "episode": "Folge {number}: {title}.",
            "outro": "Ende von Folge {number}. Danke fürs Zuhören, bis zum nächsten Mal.",
            "outro_last": "Ende von Folge {number} und Ende des Buches. Danke, dass Sie dabei waren.",
        },
        tesseract="deu",
        stopwords=_words("der die das und ist nicht ein eine mit sich auf für den dem des "
                         "auch"),
    ),
    "it": Language(
        "it", "italien", "Italiano",
        (("it-IT-DiegoNeural", "Diego (uomo)"), ("it-IT-ElsaNeural", "Elsa (donna)"),
         ("it-IT-IsabellaNeural", "Isabella (donna)")),
        labels={"question": "Domanda", "subscribe": "Iscriviti per altre domande!",
                "episode": "Episodio", "next": "Continua…", "end": "Fine", "part": "Parte",
                "by": "di", "voice": "Narrazione: voce sintetica.",
                "summary": "Riassunto del testo originale, scritto con l'aiuto di un'IA.",
                "translation": "Traduzione del testo originale, realizzata con l'aiuto di un'IA.",
                "images": "Illustrazioni (Wikimedia Commons, licenze libere):"},
        script={
            "outro_short": "Iscriviti per scoprire altre domande.",
            "welcome": "Benvenuti su {channel}.",
            "book": "{book}.",
            "book_author": "{book}, di {author}.",
            "episode": "Episodio {number}: {title}.",
            "outro": "Fine dell'episodio {number}. Grazie per l'ascolto, al prossimo episodio.",
            "outro_last": "Fine dell'episodio {number} e fine del libro. Grazie per averci seguito.",
        },
        tesseract="ita",
        stopwords=_words("il che della non per sono gli con nel alla anche come più delle "
                         "questo"),
    ),
    "pt": Language(
        "pt", "portugais", "Português",
        (("pt-BR-AntonioNeural", "Antônio (homem, Brasil)"),
         ("pt-BR-FranciscaNeural", "Francisca (mulher, Brasil)"),
         ("pt-PT-DuarteNeural", "Duarte (homem, Portugal)"),
         ("pt-PT-RaquelNeural", "Raquel (mulher, Portugal)")),
        labels={"question": "Pergunta", "subscribe": "Inscreva-se para mais perguntas!",
                "episode": "Episódio", "next": "Continua…", "end": "Fim", "part": "Parte",
                "by": "de", "voice": "Narração: voz sintética.",
                "summary": "Resumo do texto original, escrito com a ajuda de uma IA.",
                "translation": "Tradução do texto original, feita com a ajuda de uma IA.",
                "images": "Ilustrações (Wikimedia Commons, licenças livres):"},
        script={
            "outro_short": "Inscreva-se para descobrir mais perguntas.",
            "welcome": "Bem-vindos a {channel}.",
            "book": "{book}.",
            "book_author": "{book}, de {author}.",
            "episode": "Episódio {number}: {title}.",
            "outro": "Fim do episódio {number}. Obrigado por ouvir, até o próximo.",
            "outro_last": "Fim do episódio {number} e fim do livro. Obrigado por nos acompanhar.",
        },
        tesseract="por",
        stopwords=_words("os que não uma com para mais dos das são foi pelo seu sua também"),
    ),
}

CODES = tuple(LANGUAGES)
_ARABIC = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_LETTER = re.compile(r"[^\W\d_]")


def get(code: str | None) -> Language:
    """Langue demandée, ou le français par défaut."""
    return LANGUAGES.get((code or "fr").lower(), LANGUAGES["fr"])


def is_rtl_text(text: str) -> bool:
    return bool(_ARABIC.search(text))


def detect(text: str) -> str:
    """Langue probable d'un texte (échantillon du livre)."""
    sample = text[:30000]
    letters = len(_LETTER.findall(sample)) or 1
    if len(_ARABIC.findall(sample)) / letters > 0.3:
        return "ar"
    words = re.findall(r"[^\W\d_]+", sample.lower())
    scores = {code: sum(1 for w in words if w in lang.stopwords)
              for code, lang in LANGUAGES.items() if lang.stopwords}
    best = max(scores, key=scores.get)
    return best if scores[best] else "fr"


def part_titles() -> re.Pattern:
    """Titres par défaut des épisodes sans chapitre (« Partie 3 », « الجزء 3 »…)."""
    labels = sorted({re.escape(lang.labels["part"]) for lang in LANGUAGES.values()})
    return re.compile(rf"^(?:{'|'.join(labels)}) \d+$")
