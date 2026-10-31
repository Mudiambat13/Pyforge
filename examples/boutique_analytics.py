"""Projet 1 — Analytique de boutique.

Modules combinés : miniorm + schema + eventbus + cachekit + pipeline

Scénario : une boutique reçoit des ventes brutes (certaines invalides). On les
valide, on met à jour le stock en transaction, on déclenche des alertes de
rupture, puis on produit des rapports (mis en cache) et un export CSV.

    python examples/boutique_analytics.py
"""
import os
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyforge.cachekit import cached
from pyforge.eventbus import EventBus
from pyforge.miniorm import (Database, DateTimeField, IntegerField, Model,
                             RealField, TextField)
from pyforge.pipeline import Pipeline
from pyforge.schema import Integer, Schema, String, ValidationError
import datetime as dt

# ---------------------------------------------------------------- modèles
class Product(Model):
    sku = TextField(null=False, unique=True)
    name = TextField(null=False)
    category = TextField()
    price = RealField(default=0.0)
    stock = IntegerField(default=0)


class Sale(Model):
    sku = TextField(null=False)
    qty = IntegerField(null=False)
    total = RealField(null=False)
    comment = TextField()
    sold_at = DateTimeField(default=dt.datetime.now)


db = Database(":memory:")
db.create_tables(Product, Sale)

CATALOGUE = [
    ("RIZ-25", "Riz 25 kg", "alimentation", 32.0, 40),
    ("HUI-5L", "Huile végétale 5 L", "alimentation", 14.5, 25),
    ("SAV-01", "Savon de ménage", "hygiène", 1.2, 200),
    ("CHA-USB", "Chargeur USB-C", "électronique", 9.0, 15),
    ("LAM-LED", "Lampe LED solaire", "électronique", 18.0, 12),
]
for sku, name, cat, price, stock in CATALOGUE:
    Product.create(sku=sku, name=name, category=cat, price=price, stock=stock)

# ---------------------------------------------------------------- événements
bus = EventBus()
alerts: list[str] = []

@bus.subscribe("stock.low")
def on_low_stock(topic, p):
    alerts.append(f"⚠ Stock bas : {p['name']} ({p['stock']} restants)")

@bus.subscribe("sale.*")
def invalidate_reports(topic, p):
    revenue_by_category.cache_clear()      # nouveau chiffre d'affaires → cache obsolète

# ---------------------------------------------------------------- logique
SALE_SCHEMA = Schema({
    "sku": String(min_length=3),
    "qty": Integer(min=1, max=100, coerce=True),
    "comment": String(required=False),
})

LOW_STOCK = 5

def record_sale(raw: dict) -> Sale:
    data = SALE_SCHEMA.validate(raw)                       # ValidationError si invalide
    product = Product.get(sku=data["sku"])
    if product is None:
        raise LookupError(f"SKU inconnu : {data['sku']}")
    if product.stock < data["qty"]:
        raise ValueError(f"Stock insuffisant pour {product.sku} ({product.stock} < {data['qty']})")
    with db.transaction():                                 # tout ou rien
        sale = Sale.create(sku=product.sku, qty=data["qty"],
                           total=round(product.price * data["qty"], 2),
                           comment=data.get("comment"))
        product.stock -= data["qty"]
        product.save()
    bus.publish("sale.recorded", sale)
    if product.stock <= LOW_STOCK:
        bus.publish("stock.low", product.to_dict())
    return sale


@cached(ttl=60)
def revenue_by_category() -> list[tuple[str, float]]:
    cat = {p.sku: p.category for p in Product.all()}
    totals = (Pipeline.from_iterable(Sale.all())
              .group_by(lambda s: cat[s.sku],
                        agg=lambda ss: round(sum(s.total for s in ss), 2)))
    return sorted(totals.items(), key=lambda kv: -kv[1])


def main() -> None:
    rng = random.Random(7)
    skus = [c[0] for c in CATALOGUE]
    raw_sales = []
    for _ in range(30):
        raw_sales.append({"sku": rng.choice(skus), "qty": str(rng.randint(1, 6))})
    raw_sales += [
        {"sku": "XX", "qty": 1},                 # SKU trop court
        {"sku": "ZZZ-99", "qty": 2},             # SKU inconnu
        {"sku": "RIZ-25", "qty": "beaucoup"},    # quantité invalide
        {"sku": "SAV-01", "qty": 500},           # hors limites
    ]

    ok = (Pipeline.from_iterable(raw_sales)
          .map(record_sale, skip_errors=True))
    n_ok = ok.count()

    print(f"✔ {n_ok} ventes enregistrées, ✘ {len(ok.errors)} rejetées")
    for raw, exc in ok.errors[-5:]:
        msg = exc.errors if isinstance(exc, ValidationError) else str(exc)
        print(f"   - {raw} → {msg}")

    print("\nAlertes :")
    for a in dict.fromkeys(alerts):
        print("  ", a)

    print("\nChiffre d'affaires par catégorie :")
    for cat, total in revenue_by_category():
        print(f"   {cat:<14} {total:>9.2f} $")
    revenue_by_category()                      # 2e appel : servi par le cache
    print("   cache :", revenue_by_category.cache_stats())

    print("\nTop produits (quantités vendues) :")
    for sku, qty in Sale.select().group_aggregate("sku", "SUM", "qty")[:3]:
        print(f"   {sku:<8} {qty} unités")

    # Export puis relecture en flux
    path = os.path.join(tempfile.mkdtemp(), "ventes.csv")
    Pipeline.from_iterable(Sale.all()).map(
        lambda s: {"sku": s.sku, "qty": s.qty, "total": s.total}).to_csv(path)
    stats = Pipeline.from_csv(path).stats(key=lambda r: float(r["total"]))
    print(f"\nExport CSV : {path}")
    print(f"Ticket moyen {stats['mean']:.2f} $ — médiane {stats['median']:.2f} $ — max {stats['max']:.2f} $")


if __name__ == "__main__":
    main()
