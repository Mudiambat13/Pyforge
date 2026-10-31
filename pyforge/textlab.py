"""textlab — Analyse de texte légère : tokenisation, TF-IDF, mots-clés, résumé.

    tokens = tokenize("Le café de Kolwezi est excellent", stopwords=STOPWORDS)
    tfidf = TfIdf().fit(corpus)
    tfidf.keywords(document, k=5)
    summarize(long_text, n=2)
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

STOPWORDS_FR = frozenset("""
au aux avec ce ces dans de des du elle en et eux il ils je la le les leur lui ma mais me même mes moi mon ne nos notre
nous on ou par pas pour qu que qui sa se ses son sur ta te tes toi ton tu un une vos votre vous c d j l à m n s t y été
être avoir est sont suis es sommes êtes ai as a avons avez ont était sera fait faire plus très aussi comme donc car
cette cet ainsi alors après avant bien chaque encore entre peut sans sous tout tous toute toutes
""".split())

STOPWORDS_EN = frozenset("""
a an the and or but if then of at by for with about to from in out on off over under is are was were be been being
have has had do does did i you he she it we they me him her us them my your his its our their this that these those
as not no so than too very can will just into up down more most some such only own same also there here what which who
""".split())

STOPWORDS = STOPWORDS_FR | STOPWORDS_EN

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_SENT_RE = re.compile(r"(?<=[.!?…])\s+")


def remove_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def tokenize(text: str, lower: bool = True, strip_accents: bool = False,
             stopwords: frozenset[str] | set[str] | None = None, min_len: int = 2) -> list[str]:
    """Découpe en mots (lettres/chiffres Unicode). Les apostrophes séparent
    les mots : « l'été » → ['été'] (le « l » est trop court)."""
    if lower:
        text = text.lower()
    if strip_accents:
        text = remove_accents(text)
    stop = {remove_accents(w) for w in stopwords} if (stopwords and strip_accents) else stopwords
    return [t for t in _TOKEN_RE.findall(text)
            if len(t) >= min_len and not (stop and t in stop)]


def ngrams(tokens: list[str], n: int = 2) -> list[tuple[str, ...]]:
    return [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def word_freq(text: str, top: int | None = None, stopwords=STOPWORDS) -> list[tuple[str, int]]:
    counts = Counter(tokenize(text, stopwords=stopwords))
    return counts.most_common(top)


def detect_language(text: str) -> str:
    """Heuristique « fr » / « en » / « unknown » par recouvrement de mots vides."""
    tokens = tokenize(text)
    fr = sum(t in STOPWORDS_FR for t in tokens)
    en = sum(t in STOPWORDS_EN for t in tokens)
    if fr == en:
        return "unknown"
    return "fr" if fr > en else "en"


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_RE.split(text.strip()) if s.strip()]


def cosine_sparse(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    dot = sum(w * b.get(t, 0.0) for t, w in a.items())
    na = math.sqrt(sum(w * w for w in a.values()))
    nb = math.sqrt(sum(w * w for w in b.values()))
    return dot / (na * nb) if na and nb else 0.0


class TfIdf:
    """TF-IDF « sublinéaire » avec IDF lissé, vecteurs normalisés L2."""

    def __init__(self, stopwords=STOPWORDS, strip_accents: bool = True):
        self.stopwords = stopwords
        self.strip_accents = strip_accents
        self.idf: dict[str, float] = {}
        self.n_docs = 0

    def _tokens(self, text: str) -> list[str]:
        return tokenize(text, strip_accents=self.strip_accents, stopwords=self.stopwords)

    def fit(self, documents: list[str]) -> "TfIdf":
        df: Counter[str] = Counter()
        for doc in documents:
            df.update(set(self._tokens(doc)))
        self.n_docs = len(documents)
        self.idf = {t: math.log((1 + self.n_docs) / (1 + c)) + 1 for t, c in df.items()}
        return self

    def transform(self, text: str) -> dict[str, float]:
        counts = Counter(t for t in self._tokens(text) if t in self.idf)
        vec = {t: (1 + math.log(c)) * self.idf[t] for t, c in counts.items()}
        norm = math.sqrt(sum(w * w for w in vec.values()))
        return {t: w / norm for t, w in vec.items()} if norm else {}

    def keywords(self, text: str, k: int = 5) -> list[tuple[str, float]]:
        vec = self.transform(text)
        return sorted(vec.items(), key=lambda kv: (-kv[1], kv[0]))[:k]

    def similarity(self, a: str, b: str) -> float:
        return cosine_sparse(self.transform(a), self.transform(b))

    def most_similar(self, query: str, documents: list[str], k: int = 3) -> list[tuple[int, float]]:
        q = self.transform(query)
        scored = [(i, cosine_sparse(q, self.transform(d))) for i, d in enumerate(documents)]
        return sorted(scored, key=lambda x: -x[1])[:k]


def summarize(text: str, n: int = 2) -> str:
    """Résumé extractif : garde les `n` phrases les plus riches en mots
    fréquents du texte, dans leur ordre d'origine."""
    sents = split_sentences(text)
    if len(sents) <= n:
        return " ".join(sents)
    freq = Counter(tokenize(text, strip_accents=True, stopwords=STOPWORDS))
    if not freq:
        return " ".join(sents[:n])
    top = max(freq.values())
    scores = []
    for i, s in enumerate(sents):
        toks = tokenize(s, strip_accents=True, stopwords=STOPWORDS)
        score = sum(freq[t] / top for t in toks) / (len(toks) ** 0.5 or 1)
        scores.append((score, i))
    best = sorted(sorted(scores, reverse=True)[:n], key=lambda x: x[1])
    return " ".join(sents[i] for _, i in best)
