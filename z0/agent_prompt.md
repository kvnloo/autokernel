Read program.md and follow it. Setup (Phase A) is already done; start at Phase B with
`$PY orchestrate.py next`, where PY="/mnt/zer0models/venvs/unsloth/bin/python -P".
The baseline for workspace/kernel_matmul_1.py is already recorded (67.324 TFLOPS) and
kernel.py currently holds it.

Bounds for this run (owner-set, override "NEVER STOP"):
- Run at most 12 experiments in total (each = edit kernel.py, git commit, bench, record, keep or reset).
- Launch every benchmark as: $PY bench.py > run.log 2>&1   (never let its output into your context;
  grep the summary lines).
- Never edit bench.py, reference.py, extract.py, prepare.py or orchestrate.py. Never install packages.
- Never git push. Never touch files outside this directory.
- The GPU is shared with other jobs. If a result moves less than 3% from the current best, re-run
  bench once before deciding keep/revert.
- When done, run `$PY orchestrate.py status`, then print the whole of
  `cat workspace/orchestration_state.json` and a 10-line summary of what you tried and what was kept.
