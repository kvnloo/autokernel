# z0live S25 autoresearch: research org code

This folder adapts AutoKernel's loop (one editable surface, a fixed referee, keep or revert,
Amdahl prioritisation, run forever) from datacenter GPU kernels to Kevin's phone: a fully local
voice assistant on a Galaxy S25 Ultra (SM8750) that should get as close as possible to GPT-Live.
The upstream GPU flow (`../program.md`, `../bench.py`) is untouched.

## Roles (owner, 2026-11-01/02)
| Role | Who | Does |
|---|---|---|
| Driver | Opus 5.5 (the Hermes session running this file) | picks the next hypothesis, writes candidates/patches, runs the bench, integrates, reviews worker diffs, writes the ledger notes. Never grades its own lane alone. |
| Workhorses | Sol 6.1 and Terra 6.1 (Hermes `delegate_task`, `delegation.model` switched per spawn) | bounded builds, ports, evaluators, kernel patches in their own worktree/branch. No phone access. |
| Council | Opus 5.5, Fable 5.1, Astra (`gpt-6-astra`; 6.2 is not served to this account), Sol 6.1 | meta-orchestrator when progress stalls (`council.py`). Ranks attack surfaces, audits measurement validity. Never writes code. |
| Meta-research | ChatGPT deep research on the S22 (`chatgpt-driver`, tags ZLV-HW-*), answers on kvnloo/oss-factory#263; digests in `research/` | hardware-specific evidence that seeds hypotheses. |

## Surfaces the loop may change (the "kernel")
1. `candidates/<id>.json`: model, quant, threads, `cpu_mask` (taskset hex: `c0` prime, `3f` perf, `f0` 2+2), env, CLI args, objective.
2. Build variants: `phone-bundle/variants/<name>/audiocpp_cli` built by `variants/build_variants.sh` (OpenMP, armv8.6+bf16, KleidiAI, Vulkan, OpenCL-Adreno, O3/LTO).
3. Kernel patches to audio.cpp/ggml (new variant per patch; patch file committed under `patches/`).
4. New engines (QNN/LiteRT/ExecuTorch/llama.cpp) wrapped to the same CLI contract.

## Never change during an epoch
`bench_s25.py` measurement logic, `evalset/`, gates, objective of a track. A change there is a new
epoch: bump `BENCH_EPOCH`, re-run `baseline` + `aa` for every track, and note it in the ledger. (Bookkeeping-only
edits that cannot change a measured number are allowed and must be called out in the commit.)

## The loop (per experiment, ~2-4 min on the phone)
1. `python3 council.py due` -> if level > 0, convene the council first (see below).
2. Pick the track with the largest Amdahl share of the user-visible turn (today: TTS first audio and LLM first
   token; ASR/turn lanes join in epoch 2 via `STAGES.md`). Pick the hypothesis with the best
   expected gain / cost from the research digests and council ranking.
3. Write ONE change as a new candidate (or variant). One variable per experiment.
4. `python3 bench_s25.py run --candidate candidates/<id>.json` (interleaved ABBA vs incumbent, paired bootstrap CI).
   KEEP needs the CI upper bound below `1 - max(3%, A/A noise half-width)` and all gates (no errors, RTF p90 <= 0.85,
   WER <= incumbent + 2 pp, MOS >= incumbent - 0.10, s2s answers correct). Everything is appended to `state/ledger.jsonl`.
5. Every track gets 3 rounds on one direction (Theo rule): keep it, drop it, or redesign.
6. Re-run `aa` for a track after any KEEP that changes threads/placement or after 20 experiments.

## Council (stall-triggered, owner rule)
`python3 council.py due` reports the level:
- 0: keep hill-climbing.
- 1: a track has 3 non-keeps in a row (or 10 experiments since the last council): `council.py convene --rounds 1`.
- 2: stalled again after a council: `--rounds 2` (cross-examination) + fresh profiling + a new ZLV-HW research wave.
- 3: stalled a third time: 2 rounds, then retarget or stop the track and record the null result.
Read every seat's answer (`state/council/<stamp>/*.md`), reconcile disagreements with measurements, write the
resulting agenda to `research/agenda.md`, then continue the loop. A council opinion never overrides a measurement.

## Phone rules (owner: "use virtual display and adb, don't steal my focus")
- adb shell CLI only from `/data/local/tmp/z0live-ar`. No app launch, UI, mic, speaker or focus change. The bench
  aborts if focus changes without owner input and waits while the owner used the phone in the last 90 s.
- Battery >= 30% or charging; AP <= 42 C, battery <= 38 C. `/data` has ~5 GB free: the bench evicts non-active models.
- `flock /tmp/s25-bench.lock` (taken by the bench) serialises phone runs. Workers never touch the phone.
- When done for good: `adb shell rm -rf /data/local/tmp/z0live-ar`.

## Continuing unattended
Side loops and crons failed every night; continuation is Hermes native `/goal`, armed by Kevin. Goal text:
> Continue the z0live S25 autoresearch loop in /mnt/zer0models/github/autokernel/mobile per program_mobile.md:
> check `council.py due`, run the next highest-value experiment through bench_s25.py, convene the council on stalls,
> fold in new ZLV-HW research answers from oss-factory#263, commit ledger notes hourly, and post a progress
> comment on kvnloo/z0live#44 every 3 hours with measured keeps. Stop when every track has stalled at level 3.

## Files
`bench_s25.py` referee | `council.py` council + stall policy | `run_queue.sh` sequential runner | `hw/s25.json` probed
hardware | `evalset/v1.json` frozen eval set | `candidates/` | `state/` (gitignored: ledger, incumbents, runs, council)
| `research/` digests | `quality.py` (quality gate; Whisper WER + MOS proxy) | `STAGES.md` (ASR/turn/LLM lanes).
