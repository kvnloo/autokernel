#!/usr/bin/env bash
# Sequential runner. Items: base:<id> (baseline), screen:<id> (any time, nominates), <id> (idle-gated confirm),
# aa:<track> (idle-gated noise floor), confirm-queue (confirm every PROMISING id not yet confirmed).
cd "$(dirname "$0")"; export PYTHONUNBUFFERED=1
one() {
  case "$1" in
    base:*) python3 bench_s25.py baseline --tier screen --candidate "candidates/${1#base:}.json" ;;
    screen:*) python3 bench_s25.py run --tier screen --candidate "candidates/${1#screen:}.json" ;;
    aa:*) python3 bench_s25.py aa --track "${1#aa:}" ;;
    *) python3 bench_s25.py run --candidate "candidates/$1.json" ;;
  esac 2>&1 | grep --line-buffered -E "^ *(only|inc|cand) r|METRIC|\"decision\"|waiting|GATE|ABORT|Error|error" | sed -u "s|^|[$1] |"
}
for item in "$@"; do
  if [ "$item" = confirm-queue ]; then
    touch state/confirm_queue.txt state/confirmed.txt
    for id in $(sort -u state/confirm_queue.txt); do grep -qx "$id" state/confirmed.txt || { one "$id"; echo "$id" >> state/confirmed.txt; }; done
  else one "$item"; fi
done
echo QUEUE_DONE
