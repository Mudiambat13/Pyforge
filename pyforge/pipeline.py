"""pipeline — Pipelines de données paresseux et chaînables.

    (Pipeline.from_csv("ventes.csv")
        .map(lambda r: {**r, "total": float(r["total"])})
        .filter(lambda r: r["total"] > 100)
        .group_by(lambda r: r["ville"], agg=lambda rows: sum(r["total"] for r in rows)))

Un pipeline se consomme une seule fois (comme un générateur).
"""
from __future__ import annotations

import csv
import itertools
import json
import statistics
from typing import Any, Callable, Iterable, Iterator


class Pipeline:
    def __init__(self, source: Iterable[Any], errors: list | None = None):
        self._source = iter(source)
        self.errors: list[tuple[Any, Exception]] = errors if errors is not None else []

    # -- sources
    @classmethod
    def from_iterable(cls, items: Iterable[Any]) -> "Pipeline":
        return cls(items)

    @classmethod
    def from_csv(cls, path: str, delimiter: str = ",", encoding: str = "utf-8") -> "Pipeline":
        def gen():
            with open(path, newline="", encoding=encoding) as fh:
                yield from csv.DictReader(fh, delimiter=delimiter)
        return cls(gen())

    @classmethod
    def from_jsonl(cls, path: str, encoding: str = "utf-8") -> "Pipeline":
        def gen():
            with open(path, encoding=encoding) as fh:
                for line in fh:
                    if line.strip():
                        yield json.loads(line)
        return cls(gen())

    @classmethod
    def from_lines(cls, path: str, encoding: str = "utf-8") -> "Pipeline":
        def gen():
            with open(path, encoding=encoding) as fh:
                for line in fh:
                    yield line.rstrip("\n")
        return cls(gen())

    # -- transformations (paresseuses)
    def _chain(self, gen: Iterator[Any]) -> "Pipeline":
        return Pipeline(gen, self.errors)

    def map(self, fn: Callable[[Any], Any], skip_errors: bool = False) -> "Pipeline":
        """Applique `fn`. Avec skip_errors=True, les éléments en erreur sont
        ignorés et consignés dans `pipeline.errors` (partagé par la chaîne)."""
        def gen():
            for x in self._source:
                try:
                    yield fn(x)
                except Exception as exc:  # noqa: BLE001
                    if not skip_errors:
                        raise
                    self.errors.append((x, exc))
        return self._chain(gen())

    def filter(self, pred: Callable[[Any], bool]) -> "Pipeline":
        return self._chain(x for x in self._source if pred(x))

    def flat_map(self, fn: Callable[[Any], Iterable[Any]]) -> "Pipeline":
        return self._chain(y for x in self._source for y in fn(x))

    def tap(self, fn: Callable[[Any], Any]) -> "Pipeline":
        """Effet de bord (log, compteur) sans modifier le flux."""
        def gen():
            for x in self._source:
                fn(x)
                yield x
        return self._chain(gen())

    def batch(self, size: int) -> "Pipeline":
        def gen():
            it = self._source
            while chunk := list(itertools.islice(it, size)):
                yield chunk
        return self._chain(gen())

    def distinct(self, key: Callable[[Any], Any] | None = None) -> "Pipeline":
        def gen():
            seen = set()
            for x in self._source:
                k = key(x) if key else x
                if k not in seen:
                    seen.add(k)
                    yield x
        return self._chain(gen())

    def take(self, n: int) -> "Pipeline":
        return self._chain(itertools.islice(self._source, n))

    def skip(self, n: int) -> "Pipeline":
        return self._chain(itertools.islice(self._source, n, None))

    def sorted(self, key: Callable[[Any], Any] | None = None, reverse: bool = False) -> "Pipeline":
        """Matérialise le flux (tri nécessaire)."""
        return self._chain(iter(sorted(self._source, key=key, reverse=reverse)))

    def pipe(self, stage: Callable[[Iterable[Any]], Iterable[Any]]) -> "Pipeline":
        """Insère une étape personnalisée : fonction itérable → itérable."""
        return self._chain(iter(stage(self._source)))

    # -- terminaux
    def __iter__(self) -> Iterator[Any]:
        return self._source

    def to_list(self) -> list[Any]:
        return list(self._source)

    def count(self) -> int:
        return sum(1 for _ in self._source)

    def first(self, default: Any = None) -> Any:
        return next(self._source, default)

    def reduce(self, fn: Callable[[Any, Any], Any], initial: Any = None) -> Any:
        import functools
        return functools.reduce(fn, self._source) if initial is None else functools.reduce(fn, self._source, initial)

    def group_by(self, key: Callable[[Any], Any], agg: Callable[[list[Any]], Any] | None = None) -> dict:
        groups: dict[Any, list[Any]] = {}
        for x in self._source:
            groups.setdefault(key(x), []).append(x)
        return {k: agg(v) for k, v in groups.items()} if agg else groups

    def stats(self, key: Callable[[Any], float] | None = None) -> dict[str, float]:
        values = [key(x) if key else x for x in self._source]
        if not values:
            return {"count": 0}
        return {
            "count": len(values), "min": min(values), "max": max(values),
            "sum": sum(values), "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
        }

    def to_csv(self, path: str, fieldnames: list[str] | None = None, encoding: str = "utf-8") -> int:
        rows = 0
        with open(path, "w", newline="", encoding=encoding) as fh:
            writer = None
            for row in self._source:
                if writer is None:
                    writer = csv.DictWriter(fh, fieldnames or list(row))
                    writer.writeheader()
                writer.writerow(row)
                rows += 1
        return rows

    def to_jsonl(self, path: str, encoding: str = "utf-8") -> int:
        n = 0
        with open(path, "w", encoding=encoding) as fh:
            for row in self._source:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                n += 1
        return n
