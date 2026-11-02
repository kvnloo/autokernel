# Measured findings (newest first)

## 2026-11-02 LLM backends on the S25 (llama.cpp b12053 official android-arm64-snapdragon build)
llama-bench, -p 64 -n 32 -r 3, Q4_0, phone in owner use (screen-tier conditions), battery 36-37 %, 34.5-35.2 C.

| Model | Backend | Prefill tok/s (pp64) | Decode tok/s (tg32) |
|---|---|---|---|
| Qwen3-0.6B | CPU 4 threads | 561 | **135** |
| Qwen3-0.6B | CPU 6 threads | 618 | 110 |
| Qwen3-0.6B | Adreno 830 OpenCL | 911 | 81 |
| Qwen3-0.6B | Hexagon HTP v79 | **3109** | 105 |
| LFM2-1.2B | CPU 4 threads | 295 | **73** |
| LFM2-1.2B | CPU 6 threads | 361 | 67 |
| LFM2-1.2B | Adreno 830 OpenCL | 798 | 65 |
| LFM2-1.2B | Hexagon HTP v79 | **2563** | 61 |

Implications (inference, to be confirmed in the e2e lane):
- The NPU prefills 5.5x faster than the CPU. A 300-token turn context costs ~0.1 s on HTP vs ~0.5 s on CPU before the first token.
- NPU decode (105 tok/s) is far above speech rate (~3-4 tok/s of spoken English), so running the whole LLM on the NPU loses nothing
  audible and leaves the CPU free for TTS (Phonon/Pocket) and ASR: no contention between stages.
- 6 CPU threads hurt decode (memory-bound + contention); 4 threads is the CPU optimum for decode.
- OpenCL is dominated by HTP for this workload; keep the GPU free (or for a codec decoder).
