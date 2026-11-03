#!/usr/bin/env bash
# preflight each council seat with a tiny real call; writes pf-<seat>.{out,usage.json,rc}
cd "$(dirname "$0")"
run() { seat=$1 prov=$2 model=$3
  timeout 180 systemd-run --user --scope --quiet --slice=agent.slice -p MemoryMax=3G \
    hermes -p clean --provider "$prov" -m "$model" --ignore-rules -t file --usage-file "pf-$seat.usage.json" \
    -z "Reply with exactly: SEAT-OK $model" > "pf-$seat.out" 2> "pf-$seat.err"; echo $? > "pf-$seat.rc"; }
run opus anthropic claude-opus-5-5 &
run fable anthropic claude-fable-5-1 &
run astra openai-codex gpt-6-astra &
run sol openai-codex gpt-6.1-sol &
wait
for s in opus fable astra sol; do echo "== $s rc=$(cat pf-$s.rc) out=$(head -c 120 pf-$s.out | tr '\n' ' ')"; python3 -c "import json;d=json.load(open('pf-$s.usage.json'));print({k:d.get(k) for k in ('model','api_calls','failed','provider')})" 2>&1 | tail -1; done
