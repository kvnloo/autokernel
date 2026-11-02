# Proposal: benchmark epoch 2 voice stages (not applied)

`bench_s25.py` remains byte-for-byte the epoch-1 referee. This document is a
change proposal, not authorization to change the epoch-1 eval set or incumbents.
The source/CLI qualification is pinned in `../stages.json` and raw host receipts
in `../host-stage-evidence/`. No phone was contacted for this qualification.

## Preconditions / honest limits

- `audio.cpp` a01aed4 has `turn` despite omitting it from `--help`'s task list.
- It has **no standalone small-LLM text-generation task or Qwen3 text-model
  loader**. `qwen3_asr`/`qwen3_tts` are not Qwen3-0.6B chat. LFM2/LFM3 expose
  ASR/TTS/S2S; LFM3 `asr, chat=true` answers spoken input, not a text prompt, and
  is not a substitute for isolated LLM TTFT. Introduce a separately pinned
  llama.cpp executable/adapter or upstream an audio.cpp text-generation route
  before enabling `llm` or a complete cascaded `e2e`. Unsupported lanes must fail
  closed, never return a fabricated metric or silently select another task.
- Moonshine's `streaming` session at this SHA buffers input and transcribes in
  `finish_stream()`; it emits **no partials**. Its model name is not evidence of
  live streaming. Kroko is the stateful streaming candidate; Parakeet, SenseVoice,
  and Qwen3 offer buffered modes with different context/commit policies.
- Host `--audio file.wav --mode streaming` runs as fast as possible, not at audio
  cadence. Its first partial cannot measure microphone-to-partial latency.

## Exact minimal referee seams

1. **Epoch and inputs (lines 21, 30, 233).** Bump `BENCH_EPOCH=2`; load a new,
   immutable `evalset/v2.json`. Preserve v1 and archive, rather than overwrite,
   `state/{incumbents,noise,stall}.json` under epoch 1. The new set has `asr`
   (the 12 fixed WAVs plus transcript strings), `turn` (asr11/asr12 with Boolean
   expected labels), `llm` (a tokenizer-verified exactly-40-token user prompt,
   recorded raw/prompt-template token counts, fixed output cap/seed/stop policy),
   and `e2e` (the audio items). Include the fixture SHA256 and all three voices
   from `ref/asr/transcripts.json`. Keep the clips fixed after baseline starts.

2. **Assets (lines 125–131).** Add `ref` for `asr`, `turn`, `e2e` just as for
   `s2s`. Do not include `ref` for a text-only LLM. For an e2e config enumerate
   each component model and spec explicitly (`components` map), flatten their
   top-level asset directories, deduplicate with `set`, and sync each binary
   variant needed. Model residency/memory is a separate gate: sync alone is not
   proof that VAD+turn+ASR+LLM+TTS fit concurrently in 12 GB RAM.

3. **Command routing (lines 167–184).** Retain existing TTS/S2S branches.
   Do not append `--out WAV` unconditionally:
   - `asr`: `--task asr --family F --model M --backend cpu --threads 4
     --mode MODE --audio INPUT --text-out TAG.txt --log --load-progress 1`,
     plus the exact model-specific args from `stages.json`.
   - `turn`: `--task turn --family smart_turn --model M --backend cpu
     --threads 4 --mode offline --audio INPUT --log --load-progress 1`;
     retain `custom_schema_output` JSON to `TAG.turn.json` in the wrapper.
   - `vad` (optional independently scored sublane): exact stage recipe, retain
     `--segments-out TAG.segments.json` and speech start/end sample indices.
   - `llm`: dispatch only to the separately qualified text runner. Preserve
     `{IN}` as prompt-file/text and `{OUT}` as text; add verified native
     first-token, prefill token count, generated token count and decode-time
     markers. A hard-coded 40 *words* is not 40 model tokens.
   - `e2e`: launch one workflow runner per eval item, **not** a shell command
     which hard-codes the reference transcript as ASR output. The runner takes
     input PCM, obtains actual VAD segmentation and Smart Turn decisions,
     passes the actual ASR text to the LLM, then actual generated reply chunks
     to TTS. Record each component's command/model/spec hash. A process-per-stage
     fallback is a valid cold cascade but must be named `e2e_cold_processes`,
     not compared to a resident streaming assistant as if equivalent.
   Expand `{M}`, `{IN}`, `{OUT}` token by token before `shlex.quote`, preserving
   the existing explicit env/cpu-mask mechanism.

4. **Events and return codes (lines 187–219).** The epoch-1 shell pipe masks
   producer exit status and only knows audio events. Run the CLI redirected to
   a per-item raw log in the background, tail/parse that log with a separate
   reader, `wait` on the exact producer PID and emit its real `EXIT rc`. Preserve
   every raw line for host parsing. Use the following typed events rather than
   replacing everything with FIRST:
   - `START ns`; `LOADED ns` on `runtime.load.phase loaded` (requires
     `--load-progress 1`, **not** `.weights.upload_progress 1`, which may be lazy).
   - ASR `PARTIAL ns` on first **nonempty** `partial_text=` or
     `partial_text_snapshot=`. Snapshots replace previous text; deltas append.
     `FINAL ns` on `text_output=`; parse final contents from the `.txt` artifact.
   - Turn `TURN ns JSON` on `custom_schema_output=` with schema
     `smart_turn.v1`; reject malformed/missing output or probability outside [0,1].
   - VAD `SPEECH_START/SPEECH_END ns sample probability` from `event=...`;
     final artifact must be parseable, with valid sample spans.
   - `WALL value_ms` only for `session.wall_ms`, with a separate metric-source
     field. Streaming Moonshine/Earshot do not provide this marker. For them
     use timestamp boundaries or the documented native compute sum; never
     label missing wall=0 or sum both nested timing lines and parents.
   - LLM `TOKEN ns token_id` for genuinely emitted tokens, count model token
     IDs rather than words or log lines. `LLM_DONE n_tokens decode_ms`.
   - Existing TTS `FIRST_AUDIO ns` on first `audio_output_id=`; `END ns` and
     `EXIT rc` for **every** lane. Error only when rc != 0, explicit exception,
     missing/invalid required outputs or budget exhaustion—not the broad `*rror*`
     pattern that can mistake diagnostic strings for a task failure.
   `parse_events()` must retain lists for partials/tokens, first-only timestamps
   where appropriate, last final and last LOADED, and parse JSON safely.

5. **Measures/artifacts (lines 245–272, 284–295).** Name outputs by type and
   pull `txt/json/log` too; require WAV duration only for TTS/S2S/e2e. For ASR,
   input duration is the denominator. Export distinct `proc_rtf` (process start
   to exit, incl. load) and `infer_rtf` (explicit inference boundary), not
   `wall_ms / output_wav_seconds`. Record cold START→FINAL and load→FINAL;
   PARTIAL may legitimately be absent for Moonshine/offline modes. Turn reports
   decision latency (load→TURN), probability and expected/actual Boolean. LLM
   reports START→first token and prefill/request→first token separately, and
   decode tokens/s as `(N-1)/(last_token-first_token)` with N>=2; report total
   generation throughput separately if that is what the native runner measures.
   Add `OBJ_KEY` entries `asr_final`, `asr_partial`, `turn_latency`, `ttft`,
   `decode_tok_s`, `e2e_first_audio`; the throughput objective is higher-is-better,
   so either invert to ms/token before bootstrap or add an explicit direction.
   `summarize()` uses lane-specific validity rather than `audio_s` truthiness.

6. **E2E boundaries.** Fixed 16 kHz mono PCM producer must retain actual
   `INPUT_START/INPUT_END`, final voiced sample, VAD endpoint, Smart Turn decision,
   ASR final, LLM first token and TTS first PCM times. Report speech-end→first
   audio as primary conversational latency. Include the turn-detector wait and
   any buffered lookahead; zero-filling the terminal pause must not advance a
   simulated input clock for free. Add an unpaced replay throughput metric and
   a paced live-cadence latency metric as separate tracks. No mic, speaker, UI
   or focus changes are needed; write the output PCM/WAV to disk. A resident
   workflow runner needs an explicit process/model-warmup mode, not subtraction
   of arbitrary file-load markers to pretend five cold CLIs are one resident
   session. Do not enable this lane until all components actually execute.

7. **Quality and promotion (lines 320–331, 373–387).** ASR corpus WER is summed
   edit distance / summed normalized reference word count; lowercase, strip
   apostrophes and punctuation, retain digits (thus `five` vs `5` counts).
   Missing/empty transcripts and nonzero exits are failed runs, not free speed.
   Turn requires the recorded labels and an explicit false-complete gate on the
   incomplete clip: the current synthetic asr12 is classified complete at the
   default threshold and therefore **fails**. Do not relabel it to pass. Add
   natural speech and prefix-at-pause cases in a later frozen set before
   promotion. LLM must produce a nonempty, task-correct reply within budget.
   E2E requires each dependent stage's correctness and intelligible correct TTS;
   failure of an intermediate stage fails the entire item. Keep quality missing
   provisional-only and never promote `e2e` with no quality receipt.

8. **Replay tests before a new baseline.** Add host-only fixtures for absent
   FIRST, empty partials, snapshots vs deltas, missing WAV on non-audio lanes,
   turn JSON, child nonzero exit through a reader, timeout/budget exhaustion,
   missing session.wall_ms, invalid final transcript, high-is-better throughput,
   and e2e actual-output propagation (change ASR hypothesis and verify LLM input
   changes). Retain existing idle, focus, battery, thermal, ABBA, bootstrap and
   noise-floor behavior unchanged. Run five interleaved repeats per comparison;
   this host smoke study is not an S25 performance comparison.

## Smallest viable rollout

First enable `asr` and `turn` with their typed output validation and input-duration
RTF; establish epoch-2 baselines. Add the standalone text runner and token events,
then `llm`. Only then add the actual cascaded e2e workflow. The TTS/S2S epoch-1
lanes remain untouched throughout this proposal.
