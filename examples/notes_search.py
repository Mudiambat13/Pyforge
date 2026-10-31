"""Projet 3 — Moteur de recherche de notes (tolérant aux fautes).

Modules combinés : vecsearch + textlab + eventbus + pipeline

Scénario : une base de notes en français. Chaque nouvelle note est indexée
automatiquement (événement), on cherche par similarité (même avec des fautes
de frappe), on extrait des mots-clés TF-IDF, on résume les notes longues, puis
on regroupe les notes par thème avec k-means.

    python examples/notes_search.py
"""
import sys
import tempfile
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyforge.eventbus import EventBus
from pyforge.pipeline import Pipeline
from pyforge.textlab import TfIdf, detect_language, summarize
from pyforge.vecsearch import TextSearch, VectorIndex, kmeans

NOTES = [
    ("n01", "cuisine", "Recette de cuisine : poulet à la moambe. Faire revenir le poulet, ajouter la pâte de noix "
                       "de palme, laisser mijoter et servir avec du riz."),
    ("n02", "cuisine", "Recette de cuisine : beignets. Laisser la pâte lever, chauffer l'huile, frire les beignets "
                       "et servir avec du sucre."),
    ("n03", "cuisine", "Recette de cuisine : sauce pili-pili. Mixer piments, ail et citron avec de l'huile, "
                       "puis servir la sauce avec le poulet."),
    ("n04", "dev", "Bug Python en production : le code lançait une exception à cause d'une requête SQL sans "
                   "index. Corriger le code et relancer les tests."),
    ("n05", "dev", "Revue de code Django : déplacer la logique hors des vues, écrire des tests et documenter "
                   "l'API du serveur."),
    ("n06", "dev", "Déploiement du serveur : construire l'image Docker, pousser le code, relancer le conteneur "
                   "et surveiller les logs et les tests."),
    ("n07", "finance", "Budget du mois : loyer, électricité, transport. Mettre de côté dix pour cent de "
                       "l'argent reçu en épargne chaque mois."),
    ("n08", "finance", "Budget de la boutique : suivre les dépenses, comparer l'argent des ventes et les achats "
                       "de stock chaque mois, calculer la marge."),
    ("n09", "finance", "Suivi des dépenses : relancer le client pour la facture, encaisser l'argent en retard "
                       "et mettre à jour le budget du mois."),
]

bus = EventBus()
search = TextSearch()
tfidf = TfIdf().fit([text for _, _, text in NOTES])
log: list[str] = []


@bus.subscribe("note.added")
def index_note(topic, note):
    search.add(note["id"], note["text"], {"theme": note["theme"]})
    log.append(note["id"])


def show(query: str, **kw) -> None:
    print(f"\n🔎 « {query} »")
    for hit in search.search(query, k=3, **kw):
        text = search.texts[hit.id]
        print(f"   {hit.score:.2f}  [{hit.meta['theme']:<8}] {hit.id}  {text[:62]}…")


def main() -> None:
    (Pipeline.from_iterable(NOTES)
     .map(lambda n: {"id": n[0], "theme": n[1], "text": n[2]})
     .tap(lambda note: bus.publish("note.added", note))
     .count())
    print(f"{len(search)} notes indexées via le bus d'événements (langue détectée : "
          f"{detect_language(NOTES[0][2])})")

    show("poulet moambé")                               # accent différent
    show("recete de beinets")                           # fautes de frappe
    show("erreur python index sql")
    show("épargne budget mensuel")
    show("sauce", where=lambda meta: meta["theme"] == "cuisine")   # filtre sur métadonnées

    print("\n🏷  Mots-clés TF-IDF")
    for nid, theme, text in NOTES[3:6]:
        words = ", ".join(w for w, _ in tfidf.keywords(text, 4))
        print(f"   {nid} → {words}")

    long_note = " ".join(t for _, th, t in NOTES if th == "dev")
    print("\n📝 Résumé automatique des 3 notes « dev » :")
    print("  ", summarize(long_note, 2))

    print("\n🧩 Regroupement automatique (k-means, k=3)")
    ids = [n[0] for n in NOTES]
    labels, _ = kmeans([search.index.get_vector(i) for i in ids], k=3, seed=1)
    clusters: dict[int, list[str]] = {}
    for nid, lab in zip(ids, labels):
        clusters.setdefault(lab, []).append(nid)
    themes = {n[0]: n[1] for n in NOTES}
    for lab, members in sorted(clusters.items()):
        print(f"   groupe {lab}: {members} → thèmes {sorted({themes[m] for m in members})}")

    path = os.path.join(tempfile.mkdtemp(), "index.json")
    search.index.save(path)
    reloaded = VectorIndex.load(path)
    print(f"\n💾 Index sauvegardé puis rechargé : {len(reloaded)} vecteurs ({os.path.getsize(path) // 1024} Ko)")


if __name__ == "__main__":
    main()
