# Host-qualified voice stages for S25 epoch 2

Pinned audio.cpp `a01aed4a15c1da59010e48bc15fb029ecb961975`; CPU-only Release CLI, `nice -n 10`, 4 requested threads. **No phone/adb used.** Epoch-1 `bench_s25.py` is unchanged.

## Results and what to bench first

These are recipe/quality smoke measurements, not phone performance claims. RTF below is corpus post-load→final elapsed time divided by input duration; process RTF includes model loading. One process per ASR item, 12 clips/model; no warmup exclusions, uncontrolled page cache. Shared ancestor cgroup quota is **2 CPUs**, although each model requests four workers. VAD/turn use 5 repeats × 2 clips; ranges and exact commands are in `stages.json`.

| ASR primary recipe | WER | Post-load RTF | Process RTF | Median load→final ms | Partial runs |
|---|---:|---:|---:|---:|---:|
| moonshine-tiny (streaming) | 0.0421 | 0.0160 | 0.0518 | 55.65 | 0/12 |
| moonshine-small (streaming) | 0.0105 | 0.1010 | 0.2747 | 344.50 | 0/12 |
| kroko (streaming) | 0.0211 | 0.1095 | 0.1890 | 396.82 | 12/12 |
| sensevoice (offline) | 0.0105 | 0.0844 | 0.1689 | 297.22 | 0/12 |
| parakeet (offline) | 0.0105 | 0.2136 | 2.1680 | 754.59 | 0/12 |
| qwen3-asr (offline) | 0.0000 | 0.5025 | 1.5333 | 1791.47 | 0/12 |
| niagara (offline) | 0.0211 | 0.0687 | 0.2151 | 227.03 | 0/12 |

### Ranked phone qualification order

1. **Earshot VAD + Moonshine Tiny Q8** for the first latency/footprint baseline; Tiny buffers the utterance until EOF at this SHA, so pair it with VAD/turn endpointing, not a claimed partial-transcript UX.
2. **Kroko English Q8** when genuinely incremental text is required: stateful caches, real nonempty partials, and a 660 ms model finalization tail. Benchmark actual input cadence and endpoint flush latency.
3. **SenseVoice Q8 and Moonshine Small Q8** as quality/size alternatives. SenseVoice default auto chunking includes Silero helper work; compare a separately frozen no-chunk configuration if isolating just ASR.
4. **Qwen3-ASR 0.6B Q8 and Parakeet 0.6B Q8** for heavier quality alternatives. Keep loading and resident inference separate; Parakeet CPU weight upload was expensive in these process-per-item runs.
5. **Niagara 19M F32** as an offline compact-batch reference. The published package size is not inferred from parameter count.
6. **FireRed Stream-VAD, PulseVAD, MarbleNet and Silero offline** as VAD alternatives. Earshot/FireRed emit live events; PulseVAD/MarbleNet are offline here. Silero streaming failure is retained, not silently dropped.
7. **Smart Turn v3.2** for timing only until quality is fixed/qualified: it fails the synthetic incomplete-turn gate. No promotion recommendation.
8. **Standalone LLM: blocked** at this source revision. A separately pinned llama.cpp text runner or an upstream audio.cpp text route is required before an LLM or full cascade baseline. No LLM TTFT/tokens/s was fabricated.

The order is an engineering shortlist based on footprint, supported execution modes and this clean synthetic smoke set—not a statistically proven speed/accuracy ranking. No claim is made that any model is real-time on the S25 until measured there.

## VAD and turn observations

| Recipe | Mode | Median process ms | Min–max process ms | Median load→final ms |
|---|---|---:|---:|---:|
| earshot | streaming | 4.416 | 4.121–12.814 | 1.495 |
| silero-offline | offline | 26.406 | 22.900–90.806 | 18.813 |
| marblenet | offline | 17.984 | 15.676–81.383 | 10.789 |
| firered-stream | streaming | 27.056 | 25.677–37.926 | 19.147 |
| pulsevad | offline | 10.720 | 10.245–13.860 | 5.393 |
| smart-turn | offline | 67.252 | 62.637–131.572 | 44.331 |

Smart Turn outputs are deterministic across five repeats per clip:

- `asr11`: expected complete=True, actual=True, p=0.986724854, correct=True.
- `asr12`: expected complete=False, actual=True, p=0.924149752, correct=False.

The trailing-off clip is deliberately labelled incomplete. Kokoro synthesized its two clauses separately around an inserted 800 ms pause, which can create unnatural terminal prosody. This is a valid negative smoke result, not evidence of natural-conversation accuracy. Do not tune the 0.5 threshold on these two examples and call that general validation. VAD has no human frame labels in this set: detected segments prove output generation only, not F1.

## CLI and markers

`stages.json` is the machine-readable source of truth. Each validated entry contains a tokenized `cli_args_template`, model path relative to `acpp-models`, exact executed command/cwd/env, artifact hashes, observed native timing values, monotonic receipt-boundary timing, and raw log links. `{M}` is the models root, `{IN}` a 16 kHz mono PCM16 WAV, `{OUT}` the transcript/segments path (turn results are stdout JSON). Use templates with argv substitution, not string interpolation into an unquoted shell.

All recipes use `--backend cpu --threads 4 --log --load-progress 1`; run from the pinned audio.cpp checkout on host. Android packaging needs builtin current contracts or bundled model specs and helper assets; these host results alone do not validate that bundle. The `turn` task works despite not appearing in the CLI help task enumeration.

- Load done: `[TRACE ...] runtime.load.phase loaded`. Upload progress is not a reliable replacement; some weights upload lazily.
- ASR partial: first nonempty `partial_text=` delta or `partial_text_snapshot=` replacement. Final: `text_output=` plus nonempty `--text-out` artifact. Absence of partials is recorded, not assigned zero latency.
- VAD streaming: `event=speech_start sample=N probability=P` and `event=speech_end ...`; final `segments_out=...` names the validated JSON.
- Turn: `custom_schema_output={"schema":"smart_turn.v1","data":...}`; native frontend/encoder/classifier timings and `session.wall_ms` are recorded.
- **session.wall_ms is not universally an entire utterance:** FireRed/Silero streaming emit it per chunk, SenseVoice can emit it for helper/chunk subrequests, while Earshot/Moonshine streaming omit it. The manifest retains marker counts and per-run sums without assuming nested sums are exclusive.
- No first text-token marker or standalone text-generation path exists for the requested small LLMs. `gen` is primarily music/audio/video, not a generic LLM task. Negative probe return codes/logs are in `llm-blockers.json`.

## Eval set and replay

External fixtures: `/mnt/zer0models/models/zlv-tts/acpp-models/ref/asr/`: exactly 12 English 16 kHz mono PCM16 WAVs plus `transcripts.json`. Three Kokoro 0.9.4 voices (`af_heart`, `am_adam`, `bf_emma`), four clips each. `asr11` is complete and `asr12` trails off; both contain an exact inserted 800 ms mid-sentence pause. Leading/trailing silence is 100/300 ms. The manifest records transcripts, expected turn labels, durations and hashes. A copy of the metadata (not the large models) is committed.

```sh
cd /mnt/zer0models/worktrees/ak-stages
nice -n 10 env -u PYTHONPATH -u PYTHONHOME HF_HOME=/mnt/zer0models/models/hf OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 MKL_NUM_THREADS=4 /mnt/zer0models/models/zlv-tts/.venv/bin/python mobile/generate_stage_evalset.py
# Probe scripts append raw runs; use a new evidence directory or archive runs before a full replay.
env -u PYTHONPATH -u PYTHONHOME python3 mobile/host_stage_probe.py
env -u PYTHONPATH -u PYTHONHOME python3 mobile/host_stage_probe.py --streaming-smoke
env -u PYTHONPATH -u PYTHONHOME python3 mobile/probe_llm_support.py
env -u PYTHONPATH -u PYTHONHOME python3 mobile/summarize_stages.py
```

Do not regenerate or append a new qualification batch over a frozen benchmark epoch without starting a new baseline. WER strips apostrophes/punctuation and lowercases; it does not expand numeric words, so `five` vs `5` counts as one substitution. Synthetic clean speech is not an adequate noise/accent/false-endpoint acceptance corpus.

## Inventory and budget

Installed nine new packages: **3,107,754,224 payload bytes = 3.107754 decimal GB**, below the 4 GB cap. Already-installed Smart Turn/Kokoro/LFM3 and repo-bundled VAD assets were reused; no standalone unsupported LLM weights were downloaded. Download receipts and package manifests retain provenance; HTTP retry overhead is unmeasured.

`host-stage-evidence/stage-inventory.json` maps all model-spec ASR/VAD/turn families; `loaders.json` records compiled loader capabilities, including bundled Earshot/Silero/MarbleNet. The selected host recipes are a bounded CPU/English shortlist, **not** a claim that all larger, language-specific, offline-only, or experimental families were downloaded. Other available candidates include Canary 180M Flash, SAMSONE and audio8 ASR; large multilingual/diarizing/generative ASR was deferred under the budget. Inventory listings alone do not prove a model runs.

Source retrieval used the resident repository index; the Moonshine streaming-session symbol had no hit, so a bounded canonical `src/models/moonshine_asr/session.cpp` read established its EOF-buffering behavior.

## Epoch-2 work

See [`proposals/bench-epoch2.md`](proposals/bench-epoch2.md) for exact minimal seams for ASR/turn/LLM/e2e routing, typed outputs, error/exit propagation, timing boundaries, asset sync and quality gates. This proposal was **not applied**. A complete end-to-end cascade is not yet qualified because standalone LLM support is absent.
