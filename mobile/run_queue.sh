#!/usr/bin/env bash
# Runs candidate experiments one after another through the fixed bench. Usage: run_queue.sh id [id...]
cd "$(dirname "$0")"
for id in "$@"; do
  case "$id" in aa:*) python3 bench_s25.py aa --track "${id#aa:}" ;; *) python3 bench_s25.py run --candidate "candidates/$id.json" ;; esac 2>&1 | grep -E "^ *(only|inc|cand) r|METRIC|\"decision\"|waiting|GATE|ABORT|Error|error" | sed "s/^/[$id] /"
done
echo QUEUE_DONE
