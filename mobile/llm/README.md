# LLM lane (S25 voice pipeline)
- Branch: z0live/llm (worktree /worktrees/ak-llm)
- llama.cpp: d02b4c0 (pinned, cloned /models/zlv-tts/llama.cpp)
- Variants (planned): cpu, kleidiai, opencl → /models/zlv-tts/phone-bundle/llm-variants/<name>/
- GGUFs (recorded): Qwen3-0.6B Q4_0/Q8_0 (bartowski/ggml-org), Qwen3-1.7B Q4_0, LFM2-1.2B Q4_0 (LiquidAI; 696MB, sha256=2cd965...)
- Host validation: fixed 60-token prompt, /no_think, 4 threads nice -n 10
- Phone command: {BIN} -m {M} -ngl ...; time TTFT from llama-cli timing lines
- Lane proposal: mobile/proposals/bench-llm-lane.md (metric: TTFT + tok/s; gate: 8-question fixed check)
