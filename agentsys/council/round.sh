#!/usr/bin/env bash
# usage: round.sh <round-tag> <brief-file>  -> r<tag>-<seat>.{md,usage.json,rc,err}
cd "$(dirname "$0")"; tag=$1; brief=$2
run() { seat=$1 prov=$2 model=$3
  timeout 1500 systemd-run --user --scope --quiet --slice=agent.slice -p MemoryMax=3G \
    hermes -p clean --provider "$prov" -m "$model" --ignore-rules -t file,web --usage-file "r$tag-$seat.usage.json" \
    -z "$(cat "$brief")" > "r$tag-$seat.md" 2> "r$tag-$seat.err"; echo $? > "r$tag-$seat.rc"; }
run opus anthropic claude-opus-5-5 &
run fable anthropic claude-fable-5-1 &
run astra openai-codex gpt-6-astra &
run sol openai-codex gpt-6.1-sol &
wait
for s in opus fable astra sol; do echo "== $s rc=$(cat r$tag-$s.rc) bytes=$(wc -c < r$tag-$s.md)"; python3 -c "import json;d=json.load(open('r$tag-$s.usage.json'));print({k:d.get(k) for k in ('model','api_calls','failed')})" 2>&1|tail -1; done
