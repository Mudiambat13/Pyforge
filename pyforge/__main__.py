"""`python -m pyforge` : affiche le catalogue des modules."""
from . import MODULES, __version__

print(f"PyForge {__version__} — {len(MODULES)} modules\n")
for name, desc in MODULES.items():
    print(f"  {name:<10} {desc}")
print("\nExemples : python examples/boutique_analytics.py | api_monitor.py | notes_search.py")
print("Tests    : python -m unittest discover -s tests -v")
