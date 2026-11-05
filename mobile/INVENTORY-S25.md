# S25 Ultra on-device realtime voice model inventory (N03, 2026-11-05)

Device: Galaxy S25 Ultra, Snapdragon 8 Elite (SM8750). CPU, Adreno 830 GPU (OpenCL), Hexagon v79 HTP (NPU).
Everything below is already on the phone under /data/local/tmp/z0live-ar (adb shell only; no app installs).
Runtimes: audio.cpp a01aed4 `audiocpp_cli` (bin/base, CPU; opencl/vulkan/kleidiai/omp/o3lto/armv86 variants on groot),
llama.cpp in z0live-ar/llm (CPU + libggml-opencl + libggml-hexagon htp-v79; `llama-bench --list-devices` shows
GPUOpenCL Adreno 830 and HTP0).

| stage | model (track) | runtime / device | baseline status |
|---|---|---|---|
| TTS | Phonon 31M en (tts/phonon) | audio.cpp CPU | SET: warm TTFA p50 99.9 ms, RTF p50 0.76, WER 0.048, n 24/24 |
| TTS | PocketTTS en (tts/pocket) | audio.cpp CPU | SET: warm TTFA p50 130.9 ms, RTF p50 0.52, WER 0.010, n 24/24 |
| TTS | Qwen3-TTS 12Hz 0.6B (tts/qwen3) | audio.cpp CPU | candidate queued |
| S2S | LFM3-Audio-2B Q4_0 (s2s/lfm3) | audio.cpp CPU | SET: wall p50 7458 ms per turn, task acc 1.0, WER 0.060, n 8/8 |
| ASR | Moonshine streaming tiny q8, Kroko en q8, SenseVoice small q8, Niagara 19M, Parakeet TDT 0.6B q8, Qwen3-ASR 0.6B q8 | audio.cpp CPU | queued (stages-v1, 12 synthetic clips) |
| VAD | Earshot, Silero, MarbleNet, FireRed stream, PulseVAD | audio.cpp CPU | queued |
| Turn | Smart Turn v3.2 | audio.cpp CPU | queued (latency only; fails incomplete-turn gate on host) |
| LLM | Qwen3-0.6B Q4_0, LFM2-1.2B Q4_0 | llama.cpp CPU / OpenCL / Hexagon | queued: base (CPU) + gpu, npu, t6 candidates |

Not ours / not benchmarked: Samsung intellivoice and Google Voice Access (system apps, closed), Hermes mobile app (no
local model). Free space on /data is about 2 GB, so bench sync evicts unused model dirs and keeps >= 1 GB headroom.

Receipts: state/ledger.jsonl (baseline/keep/revert), state/fulltime.jsonl (telemetry, owner_yield, thermal events).
