#!/usr/bin/env bash
# Actualise les résultats depuis ce poste, puis publie data/ sur le dépôt.
# Exemple de cron (samedi et dimanche, toutes les 30 min de 9h à 20h) :
#   */30 9-20 * * 6,0  /chemin/vers/foot63/scraper/run_local.sh >> /tmp/foot63.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] && source .venv/bin/activate
NTFY_ENABLED=1 python scraper/scrape.py sync "$@"   # ajoute --discover une fois par jour pour trouver les nouveaux plateaux
git add data scraper/glyphs.json scraper/teams.json
git diff --cached --quiet || { git commit -qm "Mise à jour des résultats" && git push -q; }
