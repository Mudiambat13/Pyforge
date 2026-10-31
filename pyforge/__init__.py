"""PyForge — 10 modules Python autonomes, sans aucune dépendance externe.

Chaque module résout un problème concret et peut être utilisé seul :

    eventbus   Bus d'événements publish/subscribe avec jokers
    cachekit   Cache LRU + TTL thread-safe et décorateur @cached
    retrykit   Retry avec backoff exponentiel + circuit breaker
    miniorm    Mini ORM SQLite (modèles, requêtes chaînables, transactions)
    textlab    Analyse de texte : tokenisation, TF-IDF, mots-clés, résumé
    cronsched  Parseur cron + ordonnanceur de tâches
    pipeline   Pipelines de données paresseux (map/filter/batch/group_by…)
    schema     Validation déclarative de données (imbriquées, coercition)
    vecsearch  Recherche vectorielle, embeddings par hachage, k-means
    microweb   Micro-framework web WSGI avec routage, middlewares, client de test
"""
__version__ = "1.0.0"

from . import (  # noqa: F401
    cachekit,
    cronsched,
    eventbus,
    microweb,
    miniorm,
    pipeline,
    retrykit,
    schema,
    textlab,
    vecsearch,
)

MODULES = {
    "eventbus": "Bus d'événements publish/subscribe avec jokers",
    "cachekit": "Cache LRU + TTL thread-safe et décorateur @cached",
    "retrykit": "Retry avec backoff exponentiel + circuit breaker",
    "miniorm": "Mini ORM SQLite (modèles, requêtes chaînables, transactions)",
    "textlab": "Analyse de texte : TF-IDF, mots-clés, résumé, langue",
    "cronsched": "Parseur cron + ordonnanceur de tâches",
    "pipeline": "Pipelines de données paresseux",
    "schema": "Validation déclarative de données",
    "vecsearch": "Recherche vectorielle, embeddings par hachage, k-means",
    "microweb": "Micro-framework web WSGI avec client de test",
}

__all__ = ["MODULES", "__version__", *MODULES]
