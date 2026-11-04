#!/usr/bin/env bash
# One program.md experiment: commit kernel.py, bench in a memory-capped unit, keep or revert.
#   z0/exp.sh <kernel file in workspace/> "<description>"
# Keep rule (program.md): correctness PASS and throughput above the current best.
# Shared GPU: a result within 3% of the best is re-benched once before deciding.
set -u
cd "$(dirname "$0")/.."
PY="/mnt/zer0models/venvs/unsloth/bin/python -P"
FILE="$1"; DESC="$2"
git add kernel.py && git -c user.name="Kevin Rajan" -c user.email=7121943+kvnloo@users.noreply.github.com commit -qm "exp: $DESC" || { echo "nothing to commit"; exit 1; }

bench() {
  for attempt in 1 2 3 4; do
    # model-shape bench needs ~3.5 GB of the shared 12 GB card; wait up to 10 min for it
    for _ in $(seq 120); do
      free=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1)
      [ "${free:-0}" -ge 4500 ] && break; sleep 5
    done
    bench_once "$1-$attempt"
    grep -q "PRIMARY_NOT_BENCHED" run.log || return 0
    echo "primary size not benched (GPU memory taken by another job); retry $attempt"
  done
  TF=""
}

bench_once() {
  : > run.log
  local u="ak-fg270-bench-$$-$1"
  systemd-run --user --collect -q --unit "$u" -p WorkingDirectory="$PWD" -p MemoryMax=6G \
    -p RuntimeMaxSec=600 -p StandardOutput=append:"$PWD/run.log" -p StandardError=append:"$PWD/run.log" $PY -u bench.py \
    || { echo "systemd-run refused; running under timeout"; timeout 600 $PY -u bench.py >run.log 2>&1; }
  sleep 2; while systemctl --user is-active -q "$u" 2>/dev/null; do sleep 5; done
  COR=$(grep -m1 '^correctness:' run.log | awk '{print $2}')
  TF=$(grep -m1 '^throughput_tflops:' run.log | awk '{print $2}')
  LAT=$(grep -m1 '^latency_us:' run.log | awk '{print $2}')
  PT=$(grep -m1 '^pytorch_latency_us:' run.log | awk '{print $2}')
}
BEST=$(python3 -c "
import json;s=json.load(open('workspace/orchestration_state.json'))
k=[k for k in s['kernels'] if k['file'].endswith('$(basename "$FILE")')] if isinstance(s.get('kernels'),list) else []
print(k[0].get('best_tflops',0) if k else 0)" 2>/dev/null || echo 0)
bench 1
if grep -q "PRIMARY_NOT_BENCHED" run.log; then
  echo "GPU never had room for the model shape; not an experiment result. Undoing commit."
  git reset -q --hard HEAD~1; exit 3
fi
if [ "$COR" = PASS ] && python3 -c "import sys;b=float('$BEST');t=float('${TF:-0}');sys.exit(0 if abs(t-b)<=0.03*b else 1)"; then
  echo "within 3% of best ($TF vs $BEST): re-bench"; bench 2
fi
STATUS=revert
[ "$COR" = PASS ] && python3 -c "import sys;sys.exit(0 if float('${TF:-0}')>float('$BEST') else 1)" && STATUS=keep
[ -z "${TF:-}" ] && { STATUS=revert; TF=0; echo "CRASH"; tail -n 20 run.log; }
echo "result: correctness=$COR tflops=$TF latency_us=$LAT pytorch_us=$PT best_before=$BEST -> $STATUS"
cp run.log "z0/runs/$(git rev-parse --short HEAD).log" 2>/dev/null || { mkdir -p z0/runs; cp run.log "z0/runs/$(git rev-parse --short HEAD).log"; }
$PY orchestrate.py record "$FILE" "$TF" "$STATUS" "$DESC (lat ${LAT}us vs torch ${PT}us)" | tail -2
if [ "$STATUS" = revert ]; then git reset -q --hard HEAD~1; fi
git log --oneline -1
