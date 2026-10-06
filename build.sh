#!/usr/bin/env bash
# Render-Build: astro_engine aus Ved Chart Calc holen, dann Abhängigkeiten installieren.
set -euo pipefail

: "${ENGINE_REPO:?ENGINE_REPO fehlt (z. B. <github-name>/<ved-chart-calc-repo>)}"
REF="${ENGINE_REF:-main}"

if [ -n "${ENGINE_TOKEN:-}" ]; then
  URL="https://${ENGINE_TOKEN}@github.com/${ENGINE_REPO}.git"
else
  URL="https://github.com/${ENGINE_REPO}.git"
fi

rm -rf engine
git clone --quiet --depth 1 --branch "$REF" "$URL" engine
git -C engine rev-parse --short HEAD > ENGINE_COMMIT
rm -rf engine/.git            # Token nicht im Build-Artefakt behalten
echo "astro_engine: ${ENGINE_REPO}@$(cat ENGINE_COMMIT)"

pip install -r requirements.txt
