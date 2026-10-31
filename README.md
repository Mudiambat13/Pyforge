# PyForge 🔨

**10 modules Python autonomes, sans aucune dépendance externe.**
Chacun résout un problème concret, tient dans un seul fichier lisible, et peut être copié seul dans votre projet. Trois projets d'exemple montrent comment les combiner.

![python](https://img.shields.io/badge/python-3.10%2B-blue) ![deps](https://img.shields.io/badge/dépendances-0-brightgreen) ![tests](https://img.shields.io/badge/tests-24%20OK-brightgreen) ![license](https://img.shields.io/badge/licence-MIT-lightgrey)

---

## Sommaire

1. [Démarrage rapide](#démarrage-rapide)
2. [Structure du projet](#structure-du-projet)
3. [Les 10 modules](#les-10-modules)
4. [Les 3 projets d'exemple](#les-3-projets-dexemple)
5. [Tests](#tests)
6. [Choix de conception et limites](#choix-de-conception-et-limites)
7. [Pistes d'extension](#pistes-dextension)
8. [Licence](#licence)

---

## Démarrage rapide

Prérequis : **Python 3.10 ou plus récent**. Rien d'autre à installer.

```bash
unzip pyforge.zip && cd pyforge

python -m pyforge                              # catalogue des modules
python examples/boutique_analytics.py          # projet 1
python examples/api_monitor.py                 # projet 2
python examples/notes_search.py                # projet 3
python -m unittest discover -s tests -v        # 24 tests
```

Installation optionnelle (pour `import pyforge` depuis n'importe où) :

```bash
pip install -e .
```

Premier essai en 10 lignes :

```python
from pyforge.eventbus import EventBus
from pyforge.cachekit import cached
from pyforge.schema import Schema, String, Integer

bus = EventBus()
bus.subscribe("user.*", lambda topic, payload: print("→", topic, payload))

@cached(ttl=60)
def carre(n): return n * n

user = Schema({"name": String(min_length=2), "age": Integer(min=0, coerce=True)})
bus.publish("user.created", user.validate({"name": "Alice", "age": "30"}))
```

---

## Structure du projet

```
pyforge/
├── README.md
├── LICENSE
├── pyproject.toml
├── pyforge/                 ← la bibliothèque (10 modules)
│   ├── __init__.py
│   ├── __main__.py          ← python -m pyforge
│   ├── eventbus.py          ← 1. événements publish/subscribe
│   ├── cachekit.py          ← 2. cache LRU + TTL
│   ├── retrykit.py          ← 3. retry + circuit breaker
│   ├── miniorm.py           ← 4. mini ORM SQLite
│   ├── textlab.py           ← 5. analyse de texte
│   ├── cronsched.py         ← 6. cron + ordonnanceur
│   ├── pipeline.py          ← 7. pipelines de données
│   ├── schema.py            ← 8. validation déclarative
│   ├── vecsearch.py         ← 9. recherche vectorielle
│   └── microweb.py          ← 10. micro-framework web
├── examples/                ← 3 projets d'utilisation
│   ├── boutique_analytics.py
│   ├── api_monitor.py
│   └── notes_search.py
└── tests/
    └── test_pyforge.py
```

---

## Les 10 modules

| # | Module | En une phrase |
|---|--------|---------------|
| 1 | [`eventbus`](#1-eventbus--bus-dévénements) | Bus d'événements publish/subscribe avec jokers |
| 2 | [`cachekit`](#2-cachekit--cache-lru--ttl) | Cache LRU + TTL thread-safe et décorateur `@cached` |
| 3 | [`retrykit`](#3-retrykit--retry--circuit-breaker) | Retry avec backoff exponentiel + circuit breaker |
| 4 | [`miniorm`](#4-miniorm--mini-orm-sqlite) | Mini ORM SQLite : modèles, requêtes chaînables, transactions |
| 5 | [`textlab`](#5-textlab--analyse-de-texte) | Tokenisation, TF-IDF, mots-clés, résumé, détection de langue |
| 6 | [`cronsched`](#6-cronsched--cron--ordonnanceur) | Parseur d'expressions cron + ordonnanceur de tâches |
| 7 | [`pipeline`](#7-pipeline--pipelines-de-données-paresseux) | Pipelines de données paresseux (map, filter, batch, group_by…) |
| 8 | [`schema`](#8-schema--validation-déclarative) | Validation déclarative de données imbriquées |
| 9 | [`vecsearch`](#9-vecsearch--recherche-vectorielle) | Embeddings par hachage, index top-k, k-means |
| 10 | [`microweb`](#10-microweb--micro-framework-web-wsgi) | Micro-framework WSGI avec client de test intégré |

### 1. `eventbus` — Bus d'événements

Découple les parties d'une application : l'émetteur ne connaît pas les récepteurs. Les motifs suivent la syntaxe `fnmatch` (`*`, `?`, `[abc]`).

```python
from pyforge.eventbus import EventBus

bus = EventBus()

@bus.subscribe("order.*")                 # reçoit order.created, order.paid…
def on_order(topic, payload):
    print(topic, payload)

bus.once("order.paid", lambda t, p: print("première fois seulement"))
off = bus.subscribe("*", lambda t, p: None)   # retourne une fonction de désabonnement
off()

bus.publish("order.created", {"id": 1})       # → nombre de handlers appelés
bus.history("order.*")                        # derniers événements
```

| API | Rôle |
|-----|------|
| `subscribe(pattern, handler=None, once=False)` | Abonnement (décorateur possible) |
| `once(pattern, handler)` | Handler appelé une seule fois |
| `unsubscribe(pattern, handler)` | Désabonnement |
| `publish(topic, payload)` | Publication, retourne le nombre de handlers |
| `history(pattern)` / `listeners(topic)` | Inspection |
| `bus.errors` | Exceptions des handlers (elles n'interrompent pas les autres) |

### 2. `cachekit` — Cache LRU + TTL

Cache borné (éviction du moins récemment utilisé) avec expiration par entrée, statistiques et horloge injectable pour tester sans `sleep`.

```python
from pyforge.cachekit import cached, TTLCache

@cached(maxsize=256, ttl=60)              # mémoïsation, 60 s de validité
def taux(devise): ...

taux("EUR"); taux("EUR")
taux.cache_stats()    # {'size': 1, 'hits': 1, 'misses': 1, 'hit_rate': 0.5, ...}
taux.cache_clear()

cache = TTLCache(maxsize=1000, ttl=300)
cache.set("session:42", {"user": "alice"}, ttl=30)   # TTL propre à l'entrée
cache.get("session:42")
```

Les exceptions ne sont jamais mises en cache. Les arguments doivent être hashables.

### 3. `retrykit` — Retry + Circuit breaker

Deux briques de résilience complémentaires pour appeler des services instables.

```python
from pyforge.retrykit import retry, CircuitBreaker, CircuitOpenError

@retry(attempts=4, delay=0.2, backoff=2, jitter=0.1, exceptions=(ConnectionError,))
def appeler_api(): ...          # attentes : 0,2 s → 0,4 s → 0,8 s (+ bruit aléatoire)

breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
try:
    breaker.call(appeler_api)
except CircuitOpenError:
    ...                          # circuit ouvert : l'appel n'est même pas tenté
```

Cycle du circuit : `closed` → (3 échecs) → `open` → (30 s) → `half_open` → succès → `closed` (ou échec → `open`).
Combinez-les : `retry` à l'extérieur, `breaker.call` à l'intérieur (voir le projet 2).

### 4. `miniorm` — Mini ORM SQLite

Modèles déclaratifs, requêtes chaînables, transactions. Les noms de colonnes sont validés contre le modèle et les valeurs passent par des paramètres SQL : pas d'injection.

```python
from pyforge.miniorm import Database, Model, TextField, IntegerField, JSONField

class User(Model):
    name = TextField(null=False)
    age = IntegerField(default=0)
    prefs = JSONField()

db = Database("app.db")           # ou ":memory:"
db.create_tables(User)

alice = User.create(name="Alice", age=30, prefs={"lang": "fr"})
User.get(id=alice.id)
(User.select().where(age__gte=18, name__like="A%")
     .order_by("-age").limit(10).all())
User.select().aggregate("AVG", "age")
User.select().group_aggregate("name", "SUM", "age")      # [(groupe, valeur)]

with db.transaction():            # tout ou rien (rollback si exception)
    alice.age += 1
    alice.save()
```

Champs : `IntegerField`, `RealField`, `TextField`, `BooleanField`, `JSONField`, `DateTimeField` (options `primary_key`, `default` (valeur ou fonction), `unique`, `null`).
Opérateurs de `where` : `eq` (défaut), `ne`, `gt`, `gte`, `lt`, `lte`, `like`, `in`, `isnull`.
Une clé primaire `id` auto-incrémentée est ajoutée si vous n'en déclarez pas.

### 5. `textlab` — Analyse de texte

Prêt pour le français et l'anglais (mots vides, accents, apostrophes).

```python
from pyforge.textlab import tokenize, TfIdf, summarize, detect_language, STOPWORDS

tokenize("Le café de Kolwezi", stopwords=STOPWORDS)     # ['café', 'kolwezi']
detect_language("Je suis dans la maison")               # 'fr'

tfidf = TfIdf().fit(corpus)                             # corpus : liste de textes
tfidf.keywords(document, k=5)                           # [(mot, poids), ...]
tfidf.most_similar("ma requête", corpus, k=3)           # [(index, score), ...]

summarize(long_texte, n=2)                              # résumé extractif
```

Aussi : `ngrams`, `word_freq`, `split_sentences`, `cosine_sparse`, `remove_accents`.

### 6. `cronsched` — Cron + ordonnanceur

Expressions cron à 5 champs (`*`, listes, plages, pas, noms `mon-fri`, `jan`…), calcul de la prochaine occurrence, et ordonnanceur avec horloge injectable.

```python
import datetime as dt
from pyforge.cronsched import CronExpr, Scheduler

c = CronExpr("*/15 9-17 * * mon-fri")
c.next_after(dt.datetime.now())          # prochaine exécution
c.matches(dt.datetime(2026, 10, 27, 9, 30))

sched = Scheduler()

@sched.every(30)                          # toutes les 30 s
def ping(): ...

@sched.cron("0 8 * * 1")                  # lundi 8 h
def rapport(): ...

sched.start()                             # thread d'arrière-plan
# ou, dans votre propre boucle :  sched.run_pending()
```

Une exception dans une tâche ne tue pas l'ordonnanceur : elle est stockée dans `job.last_error`.

### 7. `pipeline` — Pipelines de données paresseux

Traite des fichiers volumineux en flux (un élément à la fois), avec gestion d'erreurs par élément.

```python
from pyforge.pipeline import Pipeline

totaux = (Pipeline.from_csv("ventes.csv")
          .map(lambda r: {**r, "total": float(r["total"])}, skip_errors=True)
          .filter(lambda r: r["total"] > 100)
          .group_by(lambda r: r["ville"], agg=lambda rows: sum(r["total"] for r in rows)))

Pipeline.from_iterable(range(10)).batch(4).to_list()     # [[0..3], [4..7], [8, 9]]
Pipeline.from_iterable(nombres).stats()                  # count, min, max, mean, median, stdev
```

Sources : `from_iterable`, `from_csv`, `from_jsonl`, `from_lines`. Étapes : `map`, `filter`, `flat_map`, `tap`, `batch`, `distinct`, `take`, `skip`, `sorted`, `pipe`. Terminaux : `to_list`, `count`, `first`, `reduce`, `group_by`, `stats`, `to_csv`, `to_jsonl`.
Un pipeline se consomme **une seule fois** (comme un générateur). Avec `skip_errors=True`, les éléments en échec sont listés dans `pipeline.errors`.

### 8. `schema` — Validation déclarative

Valide et nettoie des entrées (JSON d'API, formulaires, configuration) et rapporte **toutes** les erreurs d'un coup, avec leur chemin.

```python
from pyforge.schema import Schema, String, Email, Integer, List, Object, Boolean, ValidationError

user = Schema({
    "name":    String(min_length=2),
    "email":   Email(),
    "age":     Integer(min=0, max=130, coerce=True),      # "42" → 42
    "role":    String(choices=["admin", "user"], default="user"),
    "tags":    List(String(), required=False, default=list),
    "address": Object({"city": String()}, required=False),
})

user.validate({"name": "Al", "email": "A@B.COM", "age": "42"})
# {'name': 'Al', 'email': 'a@b.com', 'age': 42, 'role': 'user', 'tags': []}

user.errors({"name": "A", "email": "x", "tags": ["ok", 3]})
# {'name': 'au moins 2 caractère(s) requis', 'email': 'adresse e-mail invalide',
#  'age': 'champ requis', 'tags[1]': 'texte attendu'}
```

Types : `String`, `Email`, `Integer`, `Float`, `Boolean`, `List`, `Object`. Options communes : `required`, `default` (valeur ou fonction), `coerce`, `choices`, `custom` (fonction → bool). Les champs inconnus sont refusés sauf `allow_extra=True`.

### 9. `vecsearch` — Recherche vectorielle

Recherche par similarité en Python pur. Le `HashingEmbedder` ne demande aucun modèle : il projette mots et trigrammes de caractères dans un vecteur, ce qui rend la recherche **tolérante aux fautes de frappe et aux accents**.

```python
from pyforge.vecsearch import TextSearch, VectorIndex, kmeans

ts = TextSearch()
ts.add("n1", "Recette du poulet à la moambe", {"theme": "cuisine"})
ts.add("n2", "Déboguer une erreur Python", {"theme": "dev"})

ts.search("recete poulet moambé", k=3)                       # [Hit(id, score, meta), ...]
ts.search("erreur", where=lambda meta: meta["theme"] == "dev")   # filtre sur métadonnées

ts.index.save("index.json")
VectorIndex.load("index.json")

labels, centroids = kmeans(vecteurs, k=3, seed=1)            # regroupement automatique
```

Pour de **vrais embeddings sémantiques**, branchez n'importe quelle fonction `texte -> list[float]` : `TextSearch(embedder=ma_fonction)` (API d'un fournisseur, `sentence-transformers`, etc.).

### 10. `microweb` — Micro-framework web WSGI

Routage avec convertisseurs de types, JSON automatique, middlewares, gestionnaires d'erreurs et **client de test sans réseau**.

```python
from pyforge.microweb import App, HTTPError

app = App()

@app.get("/hello/<name>")
def hello(name):
    return {"message": f"Bonjour {name}"}

@app.get("/items/<int:item_id>")
def item(item_id):
    if item_id > 100:
        raise HTTPError(404, "Introuvable")
    return {"id": item_id}

@app.post("/items")
def create(request):                       # `request` est injecté s'il est déclaré
    return request.json, 201               # (corps, statut)

@app.use
def timing(request, call_next):            # middleware
    resp = call_next(request)
    resp.headers["X-App"] = "pyforge"
    return resp

@app.errorhandler(404)
def not_found(err):
    return {"erreur": err.message}

client = app.test_client()                 # tests sans ouvrir de port
client.get("/hello/Alice").json()          # {'message': 'Bonjour Alice'}

# app.run(port=8000)                       # serveur de développement
```

Convertisseurs : `<nom>`, `<int:n>`, `<float:x>`, `<path:p>`. Un handler peut retourner un `dict`/`list` (JSON), une `str`, `None` (204), un `Response`, ou un tuple `(corps, statut)`. L'application est un objet WSGI standard (compatible gunicorn, waitress…).

---

## Les 3 projets d'exemple

Chaque projet combine plusieurs modules et se lance sans configuration.

### Projet 1 — `examples/boutique_analytics.py`

**Modules :** `miniorm` + `schema` + `eventbus` + `cachekit` + `pipeline`

Une boutique reçoit des ventes brutes dont certaines sont invalides. Le script valide chaque vente (`schema`), met à jour le stock **en transaction** (`miniorm`), déclenche des alertes de stock bas (`eventbus`), calcule des rapports mis en cache et invalidés à chaque vente (`cachekit`), puis exporte un CSV relu en flux (`pipeline`).

```
✔ 27 ventes enregistrées, ✘ 7 rejetées
   - {'sku': 'XX', 'qty': 1} → {'sku': 'au moins 3 caractère(s) requis'}
   - {'sku': 'ZZZ-99', 'qty': 2} → SKU inconnu : ZZZ-99
   - {'sku': 'SAV-01', 'qty': 500} → {'qty': 'doit être <= 100'}
Alertes :
   ⚠ Stock bas : Lampe LED solaire (1 restants)
Chiffre d'affaires par catégorie :
   alimentation     1555.50 $
```

### Projet 2 — `examples/api_monitor.py`

**Modules :** `microweb` + `retrykit` + `cachekit` + `cronsched` + `eventbus` + `schema`

Une API de taux de change dépend d'un service amont instable. L'appel est protégé par **retry + circuit breaker** et mis en cache ; des tâches cron supervisent le tout ; un middleware publie chaque requête sur le bus pour produire des métriques. La démo simule la panne puis la reprise du service, avec une **horloge simulée** (aucun `sleep`, aucun réseau).

```
2) Le service amont tombe (retry x3 puis circuit ouvert)
  GET /rates/eur                     → 503 {'error': 'Service amont indisponible'}
  GET /rates/cdf (circuit ouvert)    → 503 {'error': 'Service amont isolé (circuit ouvert)'}
3) Reprise : après 30 s le circuit se rétablit
  GET /rates/cdf (half-open → ok)    → 200 {'currency': 'CDF', 'per_usd': 2800.0}
```

Pour le lancer comme un vrai serveur : `python examples/api_monitor.py --serve` puis ouvrez `http://127.0.0.1:8000/health`.

### Projet 3 — `examples/notes_search.py`

**Modules :** `vecsearch` + `textlab` + `eventbus` + `pipeline`

Un moteur de recherche de notes : chaque note ajoutée déclenche son indexation via un événement ; la recherche tolère les fautes (« recete de beinets » retrouve la recette de beignets) ; les mots-clés sont extraits par TF-IDF ; les notes longues sont résumées ; k-means regroupe les notes par thème ; l'index est sauvegardé en JSON puis rechargé.

```
🔎 « recete de beinets »
   0.25  [cuisine ] n02  Recette de cuisine : beignets. Laisser la pâte lever…
🧩 Regroupement automatique (k-means, k=3)
   groupe 0: ['n01', 'n02', 'n03'] → thèmes ['cuisine']
   groupe 1: ['n04', 'n05', 'n06'] → thèmes ['dev']
   groupe 2: ['n07', 'n08', 'n09'] → thèmes ['finance']
```

---

## Tests

```bash
python -m unittest discover -s tests -v
```

24 tests couvrent les 10 modules (cycle du circuit breaker, expiration du cache, rollback de transaction, parsing cron et prochaine occurrence, erreurs de validation imbriquées, recherche avec fautes, routage 404/405/500…). Les modules à base de temps acceptent une horloge injectable : les tests sont instantanés et déterministes.

---

## Choix de conception et limites

- **Zéro dépendance** : tout repose sur la bibliothèque standard, pour rester copiable fichier par fichier.
- **`eventbus`** est synchrone : un handler lent bloque la publication. Pour de l'asynchrone, publiez depuis un thread ou une file.
- **`miniorm`** couvre l'essentiel (CRUD, filtres, agrégats, transactions) mais pas les relations (jointures), les migrations ni les index déclaratifs.
- **`cronsched`** travaille en heure locale « naïve » (pas de fuseaux horaires) et à la minute près pour les expressions cron.
- **`vecsearch`** : le `HashingEmbedder` est *lexical* (mots + sous-mots), pas sémantique : « voiture » ne retrouvera pas « automobile ». La recherche est exhaustive (O(n)), adaptée à quelques dizaines de milliers de vecteurs.
- **`microweb`** : le serveur intégré (`wsgiref`) est destiné au développement. En production, servez l'objet `app` avec gunicorn ou waitress.
- **`textlab`** : résumé et détection de langue sont des heuristiques simples, utiles pour du prototypage, pas des modèles de langue.

## Pistes d'extension

- Ajouter des relations (`ForeignKeyField`) et des migrations à `miniorm`.
- Brancher un embedder neuronal sur `vecsearch.TextSearch` et indexer vos documents réels.
- Exposer `miniorm` via `microweb` pour obtenir une API CRUD complète en ~50 lignes.
- Ajouter un `Scheduler` persistant (jobs stockés dans SQLite via `miniorm`).
- Ajouter l'authentification (middleware) et la validation automatique avec `schema` dans `microweb`.

## Licence

MIT — voir [`LICENSE`](LICENSE).
