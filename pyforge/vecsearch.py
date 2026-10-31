"""vecsearch — Recherche vectorielle en Python pur.

    emb = HashingEmbedder(dim=1024)
    index = TextSearch(emb)
    index.add("n1", "Recette de poulet à la moambe", {"type": "cuisine"})
    index.search("poulet moambé", k=3)      # tolère les fautes de frappe

Contient : HashingEmbedder (aucun modèle à télécharger), VectorIndex (top-k
cosinus, filtres, sauvegarde JSON), TextSearch (les deux combinés) et kmeans.
Pour de vrais embeddings sémantiques, branchez n'importe quelle fonction
`texte -> list[float]` à la place du HashingEmbedder.
"""
from __future__ import annotations

import json
import math
import random
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable

from .textlab import STOPWORDS, tokenize

Vector = list[float]


def _normalize(vec: Vector) -> Vector:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else list(vec)


def dot(a: Vector, b: Vector) -> float:
    return sum(x * y for x, y in zip(a, b))


class HashingEmbedder:
    """Embedding « feature hashing » : mots + n-grammes de caractères projetés
    dans `dim` dimensions (avec signe, pour limiter les collisions).
    Déterministe, rapide, sans entraînement, robuste aux fautes de frappe."""

    def __init__(self, dim: int = 1024, ngram: int = 3, char_weight: float = 0.4):
        self.dim, self.ngram, self.char_weight = dim, ngram, char_weight

    def _add(self, vec: Vector, feature: str, weight: float) -> None:
        h = zlib.crc32(feature.encode("utf-8"))
        vec[h % self.dim] += weight if (h >> 16) & 1 else -weight

    def __call__(self, text: str) -> Vector:
        vec = [0.0] * self.dim
        for word in tokenize(text, strip_accents=True, stopwords=STOPWORDS):
            self._add(vec, "w:" + word, 1.0)
            padded = f"<{word}>"
            for i in range(len(padded) - self.ngram + 1):
                self._add(vec, "c:" + padded[i:i + self.ngram], self.char_weight)
        return _normalize(vec)


@dataclass
class Hit:
    id: str
    score: float
    meta: dict[str, Any] = field(default_factory=dict)


class VectorIndex:
    def __init__(self, dim: int):
        self.dim = dim
        self._ids: list[str] = []
        self._vecs: list[Vector] = []
        self._meta: dict[str, dict[str, Any]] = {}

    def __len__(self) -> int:
        return len(self._ids)

    def add(self, id: str, vec: Vector, meta: dict[str, Any] | None = None) -> None:
        if len(vec) != self.dim:
            raise ValueError(f"Dimension attendue {self.dim}, reçue {len(vec)}")
        vec = _normalize(vec)
        if id in self._meta:
            self._vecs[self._ids.index(id)] = vec
        else:
            self._ids.append(id)
            self._vecs.append(vec)
        self._meta[id] = dict(meta or {})

    def remove(self, id: str) -> bool:
        if id not in self._meta:
            return False
        i = self._ids.index(id)
        del self._ids[i], self._vecs[i], self._meta[id]
        return True

    def get_vector(self, id: str) -> Vector:
        return self._vecs[self._ids.index(id)]

    def search(self, vec: Vector, k: int = 5, min_score: float | None = None,
               where: Callable[[dict[str, Any]], bool] | None = None) -> list[Hit]:
        q = _normalize(vec)
        hits = []
        for id_, v in zip(self._ids, self._vecs):
            meta = self._meta[id_]
            if where and not where(meta):
                continue
            score = dot(q, v)
            if min_score is None or score >= min_score:
                hits.append(Hit(id_, round(score, 4), meta))
        hits.sort(key=lambda h: -h.score)
        return hits[:k]

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"dim": self.dim, "ids": self._ids, "vecs": self._vecs, "meta": self._meta},
                      fh, ensure_ascii=False)

    @classmethod
    def load(cls, path: str) -> "VectorIndex":
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        idx = cls(data["dim"])
        idx._ids, idx._vecs, idx._meta = data["ids"], data["vecs"], data["meta"]
        return idx


class TextSearch:
    """Index de textes : embedder + VectorIndex."""

    def __init__(self, embedder: Callable[[str], Vector] | None = None, dim: int = 1024):
        self.embedder = embedder or HashingEmbedder(dim)
        self.index = VectorIndex(len(self.embedder("probe")) if embedder else dim)
        self.texts: dict[str, str] = {}

    def add(self, id: str, text: str, meta: dict[str, Any] | None = None) -> None:
        self.texts[id] = text
        self.index.add(id, self.embedder(text), meta)

    def search(self, query: str, k: int = 5, **kw: Any) -> list[Hit]:
        return self.index.search(self.embedder(query), k, **kw)

    def __len__(self) -> int:
        return len(self.index)


def kmeans(vectors: list[Vector], k: int, iters: int = 50, seed: int = 0) -> tuple[list[int], list[Vector]]:
    """K-means (initialisation k-means++). Retourne (étiquettes, centroïdes)."""
    if not 1 <= k <= len(vectors):
        raise ValueError("k doit être compris entre 1 et le nombre de vecteurs")
    rng = random.Random(seed)

    def sqdist(a: Vector, b: Vector) -> float:
        return sum((x - y) ** 2 for x, y in zip(a, b))

    centroids = [list(rng.choice(vectors))]
    while len(centroids) < k:
        d2 = [min(sqdist(v, c) for c in centroids) for v in vectors]
        total = sum(d2)
        if total == 0:
            centroids.append(list(rng.choice(vectors)))
            continue
        r, acc = rng.random() * total, 0.0
        for v, d in zip(vectors, d2):
            acc += d
            if acc >= r:
                centroids.append(list(v))
                break
    labels = [0] * len(vectors)
    for _ in range(iters):
        new_labels = [min(range(k), key=lambda c: sqdist(v, centroids[c])) for v in vectors]
        if new_labels == labels and _ > 0:
            break
        labels = new_labels
        for c in range(k):
            members = [v for v, lab in zip(vectors, labels) if lab == c]
            if members:
                centroids[c] = [sum(col) / len(members) for col in zip(*members)]
    return labels, centroids
