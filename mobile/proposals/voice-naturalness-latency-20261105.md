# Queue refill: naturalness x latency on the S25 (z0i, 2026-11-05)

Source ask: Kevin 04:05, `z0live-phone-20261105/BRIEF-voice-lanes.md` item 1. Referee
(`bench_s25.py`, epoch 4) and `evalset/` are unchanged; everything below is either a
candidate the existing referee can run, or a seam that needs a new epoch.

## 1. Queued now (epoch 4, `candidates/`, run by `fulltime.py` under its yield/heat rules)

| id | one variable | why |
|---|---|---|
| lfm3-mmq4 | Q4_0 mmproj (154 MB vs 322 MB Q8_0); same backbone bytes | smaller quant of the encoder + codec decoder; Liquid publishes no backbone smaller than Q4_0 |
| lfm3-norepack | `lfm3_audio.cpu_repack=false` | the 7.4 s is a cold process, see 2 |
| lfm3-f0pin / lfm3-perf6pin / lfm3-prime2 | taskset f0 (4t) / 3f (6t) / c0 (2t) | core pinning; lfm3-t6/t8 (unpinned) were already queued |
| lfm3-frames4 | `stream_frames_per_event=4` | streaming chunking: fewer codec/ISTFT events |
| lfm3-tail40 | `max_audio_tail_frames=40` | closes the turn 3.2 s after text ends (default 375 frames); WER/task gates guard truncation |
| qwen3tts-f0pin | taskset f0 | natural-voice lane next to Phonon/Pocket; qwen3tts-base/t6/kleidiai were already queued |

Assets: `acpp-models/LFM3-Audio-2B-GGUF-mmq4/` (backbone hardlink, sha256 112e4acb...;
mmproj Q4_0 from rev 39670cb, sha256 467a1ee5..., provenance in `.z0i-provenance.json`).

## 2. Finding: LFM3's 7.4 s is cold wall to the *end* of the reply, not time to first audio

The lfm3-base `.events` files contain only START and END: s2s prints no LOADED, FIRST or
WALL marker under the epoch-4 pipe, so `proc_ms` = process start to process exit:
loading 1.3 GB from flash, repacking, encoding the question, and generating the whole
4-6 s reply. The number that matters for a voice turn (first audio after end of speech,
warm model) is not measured. Epoch-5 seam (needs the driver's OK, it changes the referee):

- s2s FIRST marker: the CLI writes the WAV only at exit with `--out`; use the generic
  stream sink (`app/workflow/file_sink.cpp` stream events) or `--load-progress 1` plus
  a per-event line so `load_to_first_ms` exists for s2s, then add a separate
  `s2s/lfm3-ttfa` track with objective `ttfa_warm` (objectives may not change inside a
  track).
- warm session: one resident process per block (`--request-sequence`) so model load is
  paid once, the way the phone app would.

Implemented (branch z0i/p1a2-ttfa-seam, no epoch bump: the s2s/lfm3 wall track keeps its byte-identical
epoch-4 command, test-pinned). New track `s2s/lfm3-ttfa` (candidates/lfm3-ttfa-base.json, objective
`ttfa_warm` = `runtime.load.phase loaded` -> first `stream_audio` line). FIRST needs the `streammark` build
variant = base + variants/patches/streammark.patch (one flushed stdout line per streamed chunk in
app/cli/main.cpp run_streaming; `git apply --check` clean on a01aed4). "warm" here is a primed block (one
discarded run first) plus load excluded from the number, not a resident process: the CLI's resident mode
(`--request-sequence`) is offline-only and so has no first chunk. fulltime.py skips a candidate until its
variant binary exists. Host test: tests/test_ttfa_seam.py.

## 3. End-to-end time-to-first-audio candidate (needs the epoch-2 `e2e` seam)

`proposals/bench-epoch2.md` section 3 already specifies the honest runner (actual VAD
segments -> actual ASR text -> reply -> TTS, no hard-coded transcript; a process-per-stage
fallback must be named `e2e_cold_processes`). Concrete first candidate:

    e2e-earshot-moonshine-phonon: earshot VAD (streaming) -> moonshine ASR (streaming) ->
    fixed ack text "Yeah, got it." -> phonon TTS; metric = end-of-speech sample -> first
    audio sample out, ms (objective ttfa_e2e)

The LLM hop is left out on purpose for the first cut: lane 0 speaks an ack/filler while
lanes 1-3 think (LANES.md), so VAD->ASR->first audio is the latency the user hears first.
Stage-sum floor from phone baselines already measured (cold processes, not a pipeline
measurement): moonshine 254 ms + phonon ttfa 89 ms = ~343 ms + VAD endpoint hangover
(earshot host load->final 1.5 ms; hangover is a config value, not compute).

## 4. Kitten: blocked on an Android espeak-ng

`kitten_tts` phonemizes with eSpeak-ng (`kitten_tts.espeak_library_path` /
`espeak_data_path`); the phone bundle ships no `libespeak-ng.so` or `espeak-ng-data`, and
groot has none either. Queueing it now would burn 3 attempts on a known failure. Next
step: cross-build espeak-ng 1.52 (CMake, NDK r30, arm64-v8a) plus its data on the MBP
via `fleet-build`, stage it as asset `espeak/`, then queue:

    {"id": "kitten-base", "track": "tts/kitten", "lane": "tts", "family": "kitten_tts",
     "model": "KittenTTS-GGUF/kitten-tts-mini-0.8-orig.gguf", "assets": ["espeak"],
     "extra_args": ["--session-option", "kitten_tts.espeak_library_path={M}/espeak/libespeak-ng.so",
                    "--session-option", "kitten_tts.espeak_data_path={M}/espeak/espeak-ng-data"],
     "threads": 4, "hypothesis": "baseline: KittenTTS mini 0.8 natural voice"}

## 5. Naturalness that is not PESQ (recorded separately, labelled predicted)

`mobile/naturalness.py`: UTMOS22 strong (SpeechMOS v1.2.0, VoiceMOS 2022) predicted MOS
per WAV. It writes `predicted_mos_utmos22` to its own JSON and `state/naturalness.tsv`;
it does not touch `quality.py`'s `mos` (SQUIM predicted PESQ) or the KEEP gates. Making it
a gate is an epoch change; until then it ranks voices (Phonon vs Pocket vs Qwen3-TTS vs
LFM3) next to the latency numbers. Runs off-phone on saved benchmark WAVs (MBP via
fleet-build).
