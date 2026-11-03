# Council brief, round 1 (independent opening). 2026-11-03. Facilitator: Opus (Hermes, groot).

You are one seat of a 4-model council (Opus 5.5, Fable 5.1, gpt-6-astra, gpt-6.1-sol). You do not see the other seats this round. You never write code here; you shape the research program. Label every claim **Measured** (from the data below), **Source** (give a URL or repo#issue) or **Inference**. You may use web tools to check current research; cite what you use.

## The owner's ask (Kevin, verbatim excerpts)
> "go meta & ask the council how to best think thru this problem? even though less time is spent on some of these tool calls, figuring out how to orchestrate them in parallel, optimize for disk type, personalize for hardware, run specific evals to see how hardware stress affects things. i think we need to think harder about this, see the cutting edge research on harness engineering, for example prime-intelligence pi & fx are much faster than hermes & they have support for plugins like rlm, sol-pi, and many other methods we started looking into w/ z0intelligence, but overall i think we need to optimize our overall system better to run on our hardware and know which tool to use in the right situation"

Earlier in the same thread: a unified skills/tool hub so agents know the best tool/platform/series of tools per situation; recurring failures are agents stalling, taking too long to find information, not knowing where to look. Tiered tools (fff, qmd, grep vs rg, Rust tools). "t3os has the idea of optimizing the entire pc for agents". Use kvnloo/autokernel (autoresearch keep/revert loop) for the whole system. "the most important part is that we want all of this to work exactly with our data" (event traces, conversation history, tool-call timings, hardware monitor data, k8s mesh data, which tool calls work best on which hosts). "certain grep calls might not even matter because it's only looking at 10 results... startup time matters way more... figure out which parameters are actually worth optimizing." The semantic router (vLLM-SR) was "one of the bigger unlocks"; next is memory working really well and a tiered setup of tools and platforms.

## Phase A profile already measured (Hermes only, 45 days, 204,278 tool results, 130,001 model turns, ~50% from subagent sessions)
Timing proxy: tool wall = tool msg ts − parent assistant ts; turn = assistant ts − previous msg ts.
- Model turns 606 h (p50 8.1 s, p90 32 s). Tool exec 579 h. By model: Opus 5.5 p50 6.4 s, Sol 6.1 8.2 s, Astra 13.1 s (p90 47 s).
- Per tool (hours, p50/p90 s): terminal 361 h (0.92/32); process_manage waits 71 h (0.62/180); read_file 31 h (0.18/3.1); clarify 19 h (owner wait); search_files 18 h (0.37/8.6); execute_code 15 h; patch 12 h; vision 11 h; skill_view 8 h (1.03/5.3); mcp ctx search 4 h (46/183 s!); session_search 0.28 s p50; z0_file_search (fff) 0.77 s p50.
- Terminal commands containing `sleep`: 4,816 calls, 105 h (p50 20 s, p90 280 s).
- 31% of turns are pure search/read turns = 25% of wall (282 h). Search runs of ≥5 consecutive search turns: 1,795 (92 h); ≥10: 341 (37 h).
- Near-empty search results 3,805; 67% followed immediately by another search.
- 53% of tool results come from single-call turns (no batching).
- Search engine tails: fd p90 300 s (timeouts on unscoped roots), grep small-result p90 35 s; p50s ~0.3–0.65 s, i.e. at the harness floor.
- hyperfine on groot (warm cache, hermes-agent repo): rg 5.9 ms, grep -r 17.7 ms, git grep 8.5 ms, fd 4.8 ms, find 0.9 ms, qmd --help 116 ms, gh --version 108 ms, python3 -c pass 21 ms, sqlite3 2 ms.
- Not covered yet: OMP/Claude Code/Codex timings (AgentsView: 1.05M tool calls, but only ~3.4k carry exec timestamps), hardware time series (k3s has only metrics-server, no Prometheus history), per-host comparisons.

## Harness speed study already run (z0evals PR #110, MBP, gpt-6.1-sol low effort, same model all arms)
cold TTFT median: fx custom libfx host 2.00 s; fx CLI 2.22 s; pi 2.09 s; omp 4.07 s; Hermes safe-mode 11.13 s; Hermes default 6.17 s; Hermes speed profile 3.18 s; Hermes warm ACP 1.83 s.
Small tasks wall / pass: fx-base 18.1 s 16/16, fx-cli 29.5, pi 22.2, Hermes speed-guided 31.3, Hermes speed 31.7, Hermes safe 45.0. Harder tasks: fx-base 34.4, pi 40.6, Hermes speed 51.4, fx-cli 54.7, Hermes safe 67.7 (all 12/12). Known Hermes overheads: safe-mode cold worker ~8 s; `execution_guidance` forces terminal for arithmetic (+2.4 s); big system prompt/skill catalog (fx `skill_catalog_bytes=0` cut input 15k→4.3k tokens).

## Existing related work (do not re-propose as new; build on it)
- z0evals#69 / hermes-agent#318: factorial study H0 × Z (z0intelligence decision/capability selection) × R (RLM cognitive-state virtualization / State Packets) × S (SoL-Pi ObservationPack), staged ablations; hermes-agent#312, #320, #321; oh-my-pi#76, #79 (RLM Phase II).
- z0evals studies: free-stack-v0, slm-router-v0, unified-memory-v0. z0intelligence#20 tool-calling/orchestration SLM portfolio (Nemotron-Orchestrator-8B, Qwen3.5-4B/9B, Hammer2.1, FunctionGemma-270M). Earlier SlmContext result: kNN-only router 83% route accuracy, 80.7% tool selection, no training.
- Skill hub decision (2026-10-28 analysis, unmerged): skill text in kvnloo-skills, capabilities (plugins/MCP/hooks/memory) in z0intelligence harness-adapters, distribution through kvnloo/z0 `./z0 onboard`. ~400 skills exist in the Hermes clean profile alone; skill_view is called ~9k times in 45 days.
- Laws registry with enforce/shadow/off modes and decision logs (/mnt/zer0models/laws).
- Ownership: z0intelligence = cognition/routing/memory; Kerdoios = placement/quota/capacity; Tokenomics = measurement; z0evals = studies; oss-factory = queues; .files = host IaC; z0 = component registry.
- Draft program (this council reviews it): AutoKernel loop over the agent system. Objective = median wall seconds to first correct evidence on a frozen "Locate" set mined from the 1,795 long search runs (60 dev / 40 held-out), turns as tie-break, ABBA fresh Hermes oneshots on one fixed model, paired bootstrap, KEEP below 1 − max(3%, A/A half-width). Tracks: A toolkit card; B locator memory index; C wait law (foreground sleep>30 s → shadow deny); D ctx search latency; E0 cross-harness timing; E1 host recorder; F scoped search defaults. Declared null: grep→rg swaps, CLI cold start, Rust rewrites under the 0.17 s harness floor.

## Hardware (Measured, live probes)
- groot (main host, k3s node): i9-10900KF 10C/20T, but the agent cgroup reports nproc=2 (CPU quota) while 20 CPUs are visible; 23 GB RAM, typically 5–8 GB available, zram 23 GB, memory PSI some avg10 ~3–6; RTX 3080 Ti 12 GB (llama.cpp router z0-farm, vLLM-SR in k3s). Disks: NVMe Samsung 960 EVO 500 GB (/ and /workspace, xfs: Hermes home + state DBs live here); SATA SSD WD 4 TB btrfs /mnt/zer0models (AgentsView 18.6 GB sessions.db, worktrees, datasets, containerd); SATA SSD 850 EVO 500 GB; HDD Toshiba 2.7 TB. An OOM guard kills tool processes first under pressure (my own analysis scripts got killed twice today).
- mbp (k3s node, CachyOS): where CPU builds go by owner rule (fleet-build); harness-speed-v1 ran here.
- Phones S25 Ultra (owner's daily) and S22 (factory worker) used for ChatGPT-app factory and on-device voice; on-device referee showed A/A 1.4–1.7× under owner use.
- Subscriptions: Claude (2 accounts), ChatGPT/Codex, Grok, plus free lanes (NIM, OpenRouter free, Groq, Cerebras) behind vLLM-SR.

## What I need from you (round 1)
1. **Reframe**: how should we think about this meta-problem? What is the right unit of optimization and the right objective(s)? Is a single scored objective right, or several tracks with a Pareto table? Where does "which tool in which situation" belong: prompt/skill text, a learned router (SLM/kNN), harness choice, or code-level defaults?
2. **Amdahl diagnosis** of the data above: what actually dominates, what is noise, what the profile cannot see (e.g., parallel orchestration opportunity, disk/IO stalls hidden inside "terminal", cgroup CPU quota, memory pressure, model-turn time that is really prompt size).
3. **Harness engineering frontier**: what current research/practice (2025–2026) on agent harness design, tool-use efficiency, context engineering, parallel tool calls, speculative/async tool execution, RLM-style recursive context, learned tool routers, skill retrieval, etc. is most relevant? Be concrete and cite. Should the system move work to pi/fx, port their mechanisms into Hermes, or route per task?
4. **Hardware personalization & stress**: which hardware experiments are worth running (disk placement of state DBs/indexes NVMe vs SATA vs btrfs, page cache warmth, cgroup CPU quota, memory pressure/zram, GPU co-tenancy with local SLMs, groot vs mbp placement), and how to design stress evals so the results are valid (noise, co-tenants, A/A)?
5. **Top 5 attack surfaces**, ranked, each with: predicted effect size (with units), falsifier, the exact first experiment (cheap), and which repo owns it.
6. **Measurement-validity threats** in the profile and the draft referee.
7. **What to drop or postpone**, and anything in the draft that is wrong.
Keep it under ~1,800 words. End with a 5-line "if you only do one thing this week" list.
