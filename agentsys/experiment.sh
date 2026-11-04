#!/usr/bin/env bash
# One AgentSys experiment = AutoKernel's edit -> bench -> keep/revert step, for one surface.
#
#   agentsys/experiment.sh <exp-id> <track> <candidate-arm-name> [split=dev] [reps=2]
#
# Arms live in $AGENTSYS_DATA/arms/<name>.json (incumbent.json is the current best). Tasks,
# episodes and the ledger are PRIVATE trace-derived data under $AGENTSYS_DATA and must never
# be committed to this public repository.
set -euo pipefail
exp=$1 track=$2 cand=$3 split=${4:-dev} reps=${5:-2}
D=${AGENTSYS_DATA:-/mnt/zer0models/sft-svlm/data/agentsys}
here=$(cd "$(dirname "$0")" && pwd)
eps=$D/runs/$exp-$track-$split.jsonl
python3 "$here/referee.py" run --tasks "$D/locate-v1.jsonl" --split "$split" --reps "$reps" \
  --arm "$D/arms/incumbent.json" --arm "$D/arms/$cand.json" --out "$eps" --jobs "${JOBS:-3}"
# Noise floor from the incumbent's own replicas, then the paired keep/revert verdict.
python3 "$here/loop.py" aa --episodes "$eps" --arm incumbent --ledger "$D/ledger.jsonl"
python3 "$here/loop.py" ab --episodes "$eps" --incumbent incumbent --candidate "$cand" \
  --track "$track" --surface "$(jq -r '.preamble_file // .name' "$D/arms/$cand.json")" \
  --ledger "$D/ledger.jsonl" --promote "$D"
