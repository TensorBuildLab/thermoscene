#!/usr/bin/env bash
# One-command local setup for thermoscene (macOS / Linux / WSL).
#   bash scripts/setup.sh                 # environment, case studies, tests, git repository
#   bash scripts/setup.sh --evaluations   # also rerun the paper's evaluations and figures (slower)
#   bash scripts/setup.sh --claude-code   # also install Claude Code with the official installer
set -euo pipefail
cd "$(dirname "$0")/.."
EVAL=0; CC=0
for a in "$@"; do case "$a" in --evaluations) EVAL=1 ;; --claude-code) CC=1 ;; esac; done

echo "== Python environment"
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "== EnergyPlus"
if [ -z "${THERMOSCENE_ENERGYPLUS:-}" ]; then
  for c in "$(command -v energyplus || true)" /Applications/EnergyPlus-2*/energyplus /usr/local/EnergyPlus-2*/energyplus \
           "$HOME"/EnergyPlus-2*/energyplus; do
    if [ -n "$c" ] && [ -x "$c" ]; then export THERMOSCENE_ENERGYPLUS="$c"; break; fi
  done
fi
if [ -z "${THERMOSCENE_ENERGYPLUS:-}" ]; then
  echo "EnergyPlus not found. Install EnergyPlus (tested: 22.2; https://github.com/NatLabRockies/EnergyPlus/releases),"
  echo "then: export THERMOSCENE_ENERGYPLUS=/path/to/energyplus  and rerun this script."
  exit 1
fi
echo "using $THERMOSCENE_ENERGYPLUS"

echo "== Case studies (outputs are not in git)"
for c in A_single_room B_hvac_system C_doe_medium_office; do
  python -m thermoscene build "case_studies/$c/case.yaml" --render
done

echo "== Tests"
python -m pytest -q tests

if [ "$EVAL" = 1 ]; then
  echo "== Paper evaluations and figures"
  python case_studies/topology_survey.py
  python case_studies/zone_sweep.py
  python case_studies/sensitivity.py
  python case_studies/paper_numbers.py
  python case_studies/paper_figures.py
fi

if [ ! -d .git ]; then
  echo "== Git repository"
  git init -q && git add . && git commit -qm "thermoscene: initial import"
fi

if [ "$CC" = 1 ]; then
  echo "== Claude Code (official installer)"
  curl -fsSL https://claude.ai/install.sh | bash
fi

cat <<MSG

Done. Next:
  source .venv/bin/activate && export THERMOSCENE_ENERGYPLUS="$THERMOSCENE_ENERGYPLUS"
  git remote add origin <your GitHub repo URL> && git push -u origin main     # optional
  claude                                                                       # start Claude Code here
MSG
