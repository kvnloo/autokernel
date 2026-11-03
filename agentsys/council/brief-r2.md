# Council brief, round 2 (cross-examination). Fresh invocation; you see everyone's round-1 text.

The original round-1 brief follows first, then the four openings, then NEW measurements the facilitator ran to settle falsifiers you proposed. Rules: quote peers briefly when you agree or disagree; give an explicit list of what you RETRACT, NARROW or DEFEND from your own round-1 text; list remaining disagreements. Label claims Measured/Source/Inference. Under ~1,200 words. No code.

Deliverable this round: a converged program the owner can approve, as
(a) the objective(s) and KEEP rule, in one paragraph;
(b) an ordered experiment list for the next 2 weeks (max 8 items), each with owner repo, cost, falsifier;
(c) the hardware/stress experiment design in ≤6 lines;
(d) the harness decision (port vs route vs migrate) in ≤4 lines;
(e) the "which tool when" architecture in ≤5 lines, consistent with the existing skill-hub ownership split;
(f) disagreements that remain and what single measurement would settle each.

## NEW measurements (facilitator, 2026-11-03, Measured)
1. Timestamp semantics: Hermes writes the assistant message when generation completes. Evidence: `memory` tool p50 0.05 s and `read_file` p50 0.18 s under the tool-wall proxy; if the stamp marked stream start, these would include generation time (seconds). So tool wall excludes the model's own generation; parallel siblings still share one parent stamp.
2. CPU quota: the whole `agent.slice` (all agent harnesses and their tools together) has `cpu.max = 200000 100000`, i.e. 2 CPUs shared by every agent on a 20-thread host; sched_getaffinity = 20. Counter since slice (re)creation: nr_periods 542, nr_throttled 12, throttled 1.94 s — short window, needs the 24 h log.
3. Kills: over 204,740 tool results in 45 days, SIGKILL/exit 137 = 93, SIGTERM/143 = 137. OOM kills are not a meaningful source of retries at this scale.
4. Sleeps: the 4,816 sleep-containing terminal calls declare 96.1 h of literal `sleep N` out of their 105.3 h wall (91%), so the wall IS the sleep, not the job. Classified by what the command does after/around the sleep: log/file watching (tail/grep/cat/ls) 2,305 calls 64.9 h; other 1,586 / 21.9 h; CI/gh 360 / 11.9 h; k8s 71 / 2.5 h; phone/adb 204 / 1.0 h; build/test 98 / 1.4 h. Only 110 of them were background calls. Overshoot (job done before wake) is not yet measurable from stored data.
5. Turn time vs tokens (session-level, ecological; per-call usage is NOT stored by Hermes, only per-session totals): average context per API call is large — Opus 214k tokens, Sol 151k, Astra 118k, with 96–97% cache-read. Across sessions of the same model, median turn time barely tracks cached context (Sol +0.026 s per 1k context tokens; Opus +0.008 s/1k; Astra ≈0), tracks uncached tokens for Astra (+1.0 s per 1k uncached) and strongly tracks output tokens per call (Astra +19 s per 1k output, Sol +9.7 s, Opus +2.3 s). Quartiles: Sol 6.5 s (ctx<85k) vs 9.2 s (ctx>148k); Opus 5.0 s vs 6.6 s. Inference to test: for GPT seats, reasoning/output length and cache misses drive turn time more than prompt size.

=================== ROUND-1 BRIEF
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

=================== ROUND-1 OPENING: opus
Council seat: Opus 5.5, round 1

Labels: [M] Measured from the brief, [S] Source, [I] Inference.

1. Reframe

The unit to optimize is the time an episode spends on its critical path to the first correct evidence, not the time of each tool call. [I] Hermes runs about 50% of its sessions as subagents [M], so adding model hours (606 h) to tool hours (579 h) counts parallel work twice and counts a parent's wait on its child twice. Before ranking anything, rebuild each episode's critical path, with child sessions nested inside the parent's delegate call.

Objectives: keep one scored referee per track, and put a Pareto table above them with five columns: wall time to evidence, pass rate, tokens or dollars, owner-attention minutes (clarify alone is 19 h [M]), and failure or retry rate. A single scalar invites trading correctness or owner time for speed. [I]

Use the mean or total for the headline number, not the median. Hours add up as mean × count, so the median hides the tails, and the tails are where the hours are: terminal p50 is 0.92 s but p90 is 32 s; process waits are 0.62 s vs 180 s [M]. Report the median as a robustness check only. [I]

Where "which tool in which situation" belongs, cheapest and most reliable first:
- (a) Code defaults: scoped roots, timeouts, a wait-until primitive, result caps. Deterministic, and no model has to remember anything.
- (b) Harness mechanisms: parallel or batched tool execution, warm workers, a small prompt.
- (c) A short toolkit card retrieved just in time per situation, instead of a catalog of about 400 skills. Anthropic's context-engineering guidance makes the same point: smallest high-signal context, loaded just in time [S: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents].
- (d) A learned router (kNN or SLM), only once the traces supply labels. The kNN router already reached 80.7% tool selection [M].
Prompt prose is the weakest layer; use it only for what (a) to (d) can't encode. [I]

2. Amdahl diagnosis

What dominates:
- Model-turn count × per-turn latency. 130k turns at p50 8.1 s = 606 h [M]. The lever is fewer turns and cheaper turns. Faster tools won't move it. [I]
- Waiting. Sleep commands (105 h) plus process waits (71 h) = 176 h, about 30% of tool time [M]. This is an orchestration problem, not a speed problem: wake on event instead of polling, and do other work while waiting. [I]
- Search and read turns: 25% of wall, 282 h [M]. Search tools have p50 0.18 to 0.77 s [M], so most of that time is model turns between searches. The lever is the number of search turns per locate. Engine speed is not it. [I]

Noise or near-null: engine micro-speed (rg 5.9 ms vs grep 17.7 ms [M], both under the about 0.17 s harness floor) and subprocess CLI cold start (qmd 116 ms) [M].

Real tails, cheap to fix: fd p90 300 s from unscoped roots, ctx search p50 46 s, and grep with few results at p90 35 s [M]. Only about 4 h in total for ctx, but each instance stalls an agent, and stalls are what the owner complains about. [I]

What the profile can't see:
- Parallel opportunity. 53% of results come from single-call turns [M], but we don't know how many next calls were actually independent of the previous result. That fraction is the upper bound for batching. [I]
- Prompt size and prefix-cache hits. Turn latency likely tracks input tokens and cache misses rather than the model alone; fx's prompt cut from 15k to 4.3k tokens is evidence [M]. Provider usage fields can measure this. [I]
- The CPU quota. nproc=2 against 20 visible CPUs [M] probably throttles tests, builds, rg threads and Python inside "terminal". Read cpu.stat nr_throttled and throttled_usec. [I]
- Memory pressure and OOM kills. PSI some avg10 is 3 to 6, and the guard kills tool processes first [M]. The visible cost is retries and re-runs, attributed to the wrong cause. [I]
- IO placement. AgentsView's 18.6 GB SQLite and the worktrees sit on SATA btrfs [M]. SQLite on copy-on-write btrfs fragments; that plus a cold page cache is a plausible cause of the 46 s ctx search. [I, needs test]
- Terminal's internal mix: build, test, git, network, CI polling. Split terminal time by command class before attacking it. [I]

3. Harness engineering frontier

- Parallel or planned tool calls. LLMCompiler plans a dependency graph and runs independent calls together [S: arXiv 2312.04511]. Speculative tool execution: PASTE exploits recurring tool sequences and reports −48.5% task time [S: https://arxiv.org/html/2603.18897v1]; Speculative Actions predicts the next action with a fast model, up to 55% accuracy and −20% latency [S: https://arxiv.org/html/2510.04371v2]; SPAgent speculates the early evidence-gathering steps of search agents, up to 1.65× [S: https://arxiv.org/html/2511.20048v1].
  Relevance: our search runs (1,795 runs of ≥5 turns [M]) are exactly the "early evidence-gathering" pattern. Read-only search is side-effect-free, so speculating on it is safe. [I]
- Context engineering: just-in-time retrieval, tool-result clearing, subagents that return 1–2k-token summaries [S: Anthropic link above]. This supports a toolkit card over the catalog, and summary-only subagent returns.
- RLM treats the prompt as an environment and recurses over it [S: arXiv 2512.24601]. It is a quality and long-context mechanism at about equal cost, not a latency mechanism. Keep it in the #318 quality factorial; don't score it on the speed referee. [I]
- Prompt and skill self-optimization (GEPA [S: arXiv 2507.19457], ACE [S: arXiv 2510.04618]) suits the toolkit card in Track A: evolve the card against the Locate referee. [I]

Recommendation on pi and fx: port their mechanisms into Hermes rather than migrate. Measured gaps: small tasks fx-base 18.1 s vs Hermes speed 31.7 s; cold TTFT 2.0 s vs 3.18 s, and 1.83 s for warm ACP [M]. Most of the gap is identified: catalog bytes, the forced-terminal guidance, cold workers [M]. Hermes owns memory, laws and skills, which pi and fx lack. Keep pi and fx as reference arms in every referee run, and route the leaf subagents that need no memory or skills (pure locate or edit) to an fx-like lean profile. [I]

4. Hardware personalization and stress

Worth running, in order:
- (1) CPU quota: nproc 2 vs 8 vs unbounded on a fixed test/build/rg command set, then on the Locate set. A cheap read of cpu.stat comes first; if there is no throttling, drop this. [I]
- (2) Memory: count OOM-guard kills per day per tool, and run the referee with and without a controlled stress-ng --vm co-tenant. [I]
- (3) Disk placement for the ctx index and AgentsView DB: NVMe xfs vs SATA btrfs vs btrfs with nodatacow (chattr +C on a fresh copy), cold vs warm cache. Use vmtouch or fadvise for cold-cache trials; drop_caches needs root. [I]
- (4) GPU co-tenancy: vLLM-SR route latency while llama.cpp z0-farm is serving. Only matters if SR sits on the hot path for every turn. [I]
- (5) groot vs mbp, only for build- and test-heavy tasks.
Skip phone stress for this program. The on-device A/A of 1.4–1.7× [M] is too noisy, and existing uiautomator2 evals should be reused if it is ever needed (owner correction).

Validity design:
- A/A before any A/B, and set the minimum detectable effect from the A/A spread.
- Randomized, interleaved order in time-of-day blocks.
- Log covariates for every trial: PSI cpu/mem/io, cpu.stat throttling, iostat await, GPU utilization, co-tenant process list. Covariate-adjust or stratify on them, and exclude trials where a co-tenant spiked.
- Inject stress as a controlled factor level with stress-ng, not as uncontrolled background.
- Use a fixed provider endpoint and log the provider-side TTFT, so network and provider variance is separated from host effects.

5. Top 5 attack surfaces, ranked

1) Waits become events plus parallel work. Owner: hermes-agent (wait-until on process exit, file, log regex, or PR state), with laws in shadow mode for Track C.
- Effect: −30 to −50% of the 176 h, i.e. 50–85 h per 45 days of agent wall. [I]
- Falsifier: polling overshoot (job finished → next poll) is under 15% of sleep time, and the agent had no independent work queued.
- First experiment: classify the 4,816 sleep calls by target (CI, build, deploy, pod) and compute overshoot from the following tool output or logs. This is SQL only, no new runs.

2) Fewer turns through batching and dependency-aware parallel calls. Owner: hermes-agent harness; z0intelligence for the planner.
- Effect: −10 to −20% of turns on multi-search episodes; each avoided turn saves about 8 s. [I]
- Falsifier: under 25% of consecutive single-call pairs are argument-independent.
- First experiment: on mined traces, label pairs as independent when the next call's arguments contain no token from the previous result. This gives the parallelism upper bound.

3) Locator memory and toolkit card (Tracks A and B). Owner: z0intelligence memory, kvnloo-skills for the card.
- Run an oracle upper bound first: Locate tasks with the correct path or entry point injected into the prompt.
- Effect: if the oracle cuts wall by ≥40%, B is worth building; predicted −25 to −40% wall on ≥5-turn runs. [I]
- Falsifier: the oracle saves under 15%, which means the model-turn floor dominates, not "where to look".

4) Prompt size and cache → turn latency. Owner: hermes-agent profiles, plus Tokenomics for measurement.
- Effect: −1 to −3 s on turns that miss the cache; fx evidence: about 13 s per small task. [I]
- Falsifier: regressing turn latency on input tokens and cache_read tokens gives a slope under 0.1 s per 1k uncached tokens.
- First experiment: that regression, from provider usage fields already in the state DBs.

5) Host tails: CPU quota, OOM, and ctx/fd tails. Owners: .files and Kerdoios for the quota and placement; Track D for ctx; hermes-agent for Track F's scoped and timed-out fd defaults.
- Effect: removes the 300 s fd p90 entirely, ctx p50 from 46 s to under 2 s, and test commands 1.5–4× faster if throttled. [I]
- Falsifier: zero throttled_usec over 24 h and a ctx cold/warm ratio under 2×, which makes it an algorithm problem rather than IO.
- First experiment: 24 h cpu.stat and PSI logging, plus a ctx search timed cold vs warm on the current disk vs an NVMe copy.

6. Measurement-validity threats

Profile:
- If the parent assistant timestamp marks the start of the stream, "tool wall" includes the model's own generation time. Verify which end it marks.
- Parallel calls in one turn share the parent timestamp, so their wall times overlap and adding them overcounts.
- Delegate or subagent waits are counted both in the parent and in the child.
- clarify and process waits mix owner idle time with agent time.
- OOM-killed and aborted sessions are under-represented, which biases toward survivors.
- Hermes only, and AgentsView has only about 3.4k timed calls, so cross-harness claims are not yet measurable.

Draft referee:
- (a) Median as the objective hides tails; use a trimmed mean with p90 as a guard.
- (b) Locate tasks mined from the longest runs have their hardness tied to session context that a fresh oneshot lacks, so the task changes. Replay with the original context prefix, or at least the user turn plus the repo snapshot.
- (c) The repo has drifted since mining. Pin worktrees at the original commit, and grade "correct evidence" against a written answer key, not a judge model.
- (d) A 3% threshold with 40 held-out tasks is probably underpowered. Compute the minimum detectable effect from A/A before freezing the threshold.
- (e) One fixed model is fine for the referee, but each KEEP needs a second-model spot check, or results overfit Opus behavior.
- (f) Run the AutoKernel loop on dev only; held-out is touched once per candidate family, otherwise it leaks.

7. Drop, postpone, correct

- Drop: grep→rg swaps, Rust rewrites under the harness floor, and CLI micro cold start (agree with the draft).
- Correct the draft: "CLI cold start = null" must not cover harness cold start. Hermes at 6.17–11.13 s vs 1.83 s warm [M] is a real lever, especially multiplied by about 50% subagent sessions. Make it a track: warm worker pool or ACP for subagents.
- Postpone: SLM router training until rows 2 and 3 produce labels; RLM and SoL-Pi on the speed referee (they stay in the #318 quality factorial); GPU co-tenancy until the CPU-quota check is in; phone experiments.
- Reorder: E0 and E1 (cross-harness timing and the host recorder) come before Tracks A–F. Without host covariates, every KEEP or REVERT on groot is confounded by PSI and throttling.

If you only do one thing this week:
1. Rebuild the critical path (nest children in parents, split terminal by command class), then rerun the Amdahl table on the mean.
2. SQL-classify the 4,816 sleeps and measure polling overshoot (row 1's falsifier).
3. Log cpu.stat throttling and PSI for 24 h on groot, and time ctx search cold vs warm on SATA btrfs vs NVMe.
4. Run the oracle-path upper bound on 20 Locate tasks before building locator memory.
5. Run an A/A of the referee to get its minimum detectable effect; only then freeze the KEEP threshold.

Standing-rule check: I saved nothing. This session has no memory or skill_manage tool, and the brief restates existing direction rather than a new standing rule.

=================== ROUND-1 OPENING: fable
Council seat: Fable 5.1 — round 1, independent.

(Standing-rule check: the only candidate rule here is the council protocol itself — label claims Measured/Source/Inference, no code in council briefs. This session exposes no memory/skill_manage tool (tool_search returned none), so nothing was saved; facilitator should record it if it is meant to persist.)

1. REFRAME

The unit of optimization is the turn, not the tool call. Measured: model turns are 606 h of ~1,185 h total; p50 turn 8.1 s vs tool p50s of 0.2–0.9 s. Every tool call you avoid saves ~8 s of model time plus its own 0.3 s; every tool call you make 2x faster saves 0.15 s. The right objective is therefore episode-level: wall seconds AND turn count to first correct evidence (co-primary, not tie-break — turns ≈ tokens ≈ dollars and is the less noisy of the two), gated on pass rate not dropping. A single scored objective is right for the keep/revert loop (AutoKernel needs one number), but the report must be a Pareto table: wall, turns, input tokens, pass rate, and p90 wall. P90 matters because Kevin's named failure mode is stalling, and stalls live in tails that a median hides.

"Which tool in which situation" belongs in three layers, by how often the decision is actually a decision:
  a. Code-level defaults, zero tokens: scope roots, timeouts, rg-not-grep, result caps. Never ask the model.
  b. A retrieved per-step toolkit card (ITR-style, Source: arxiv.org/abs/2602.17046) rather than a 400-skill catalog in the prompt. The existing kNN router (Measured earlier: 83% route accuracy, no training) is already good enough to pick the 3–5 relevant skills/tools per step; semantic MCP tool discovery reports 97% hit@3 under 91 ms (Source: arxiv.org/abs/2603.20313).
  c. Harness/model choice per task class, via vLLM-SR — only after Hermes's own floor is fixed, otherwise you route around a bug instead of fixing it.

2. AMDAHL DIAGNOSIS

Measured shares of ~1,185 h: model turns 51%; terminal 30% (of which sleep 105 h = 9% of everything); process_manage waits 6%; pure locate turns 25% (282 h, overlapping the above); search_files engine time 1.5%; ctx search 0.3%.

What dominates: (i) number of turns × prompt size, (ii) waiting (sleep + process_manage = 176 h, 15%), (iii) locate chains. What is noise for the objective: search engine choice (Measured p50 at the 0.17 s floor), CLI cold starts, Rust rewrites — the draft's null list is correct.

What the profile cannot see:
  - Parallelism headroom. Measured: 53% of tool results come from single-call turns. Inference: if even a third of the locate turns could carry 2–3 independent calls, that is ~40–60 h of model time over 45 days, more than every search-engine optimization combined.
  - Double counting from subagents (50% of data). A parent's "tool wall" on a delegate call contains the child's entire run. Hours are inflated and the parent/child split is unknown.
  - Prompt-size effect on turn time. Hermes speed profile cut TTFT 6.17→3.18 s and fx cut input 15k→4.3k tokens (Measured, z0evals#110). You have 130k turns with input token counts already; regress turn time on input tokens before building anything.
  - IO stalls. Terminal p90 32 s hides builds, cold page cache on the 4 TB SATA btrfs, and the 18.6 GB sessions.db. ctx search p50 46 s is almost certainly IO/CoW-bound (Inference; btrfs docs recommend nodatacow for databases, Source: btrfs.readthedocs.io, mount options / "nodatacow").
  - cgroup quota. Measured nproc=2 on groot. Every "parallel" tool call, rg thread, and build in the 45-day profile ran on 2 CPUs. Tail numbers are quota-shaped.
  - OOM kills. A killed tool shows up as an extra retry turn, not as a slow tool. Count exit-137/"Killed" tool results; it's a free query.
  - Provider latency. Astra p50 13.1 s vs Opus 6.4 s is probably provider/effort, not harness. The timing proxy conflates queue, network, prefill and decode.

3. HARNESS FRONTIER (what to take, what to leave)

  - Prompt/tool diet is the largest portable win. Empirical harness study (Source: arxiv.org/abs/2609.20804, 176 settings, SWE-Bench/Terminal-Bench): rule-based elision of stale tool results before any LLM summarization is the most efficient context strategy; recoverable-elision machinery is rarely used; bash-capable models run cheaper on bash-only interfaces. Anthropic's managed-agents post (Source: anthropic.com/engineering/managed-agents) makes the same structural point: session log ≠ context window; harness assumptions go stale per model, so keep them swappable and measured.
  - Tool/instruction retrieval: ITR (above) and Tool Attention (Source: arxiv.org/abs/2604.21816 — note its latency/success numbers are projected, not live) both show 95% token cuts; treat the token cut as real, the downstream claims as hypotheses to test on the Locate set.
  - Speculative tool execution: client-side speculation with a small draft model gives 6–21% per-turn savings, bounded at 2x, and only when tool latency ≈ generation time, 0.5–3 s (Source: arxiv.org/abs/2512.15834). PASTE exploits recurring sequences (search→read, clone→checkout) with slack-only scheduling, 48.5% task-time reduction in their serving setting (Source: arxiv.org/abs/2603.18897). Speculative Interaction Agents add the rule you want: read-only tools safe to speculate, write tools held until commit (Source: arxiv.org/abs/2605.13360). Inference for your data: with tool p50 0.3 s against 8 s turns, speculation buys nothing on the median; it pays only on the terminal/process tail. Postpone.
  - RLM (Source: arxiv.org/abs/2512.24601): treats the long prompt as an environment, recursive sub-calls; +13% vs Claude Code on long-context tasks. Already in your H0×Z×R×S factorial; keep it there, don't fork it into this program.
  - Move to pi/fx, port, or route? Measured: fx-base 18.1 s vs Hermes speed 31.7 s on small tasks, same model; known causes are cold worker, forced-terminal guidance, prompt size. Inference: those are mechanisms, not architecture. Port them (warm worker default, skill catalog bytes→0 with retrieval, drop execution_guidance for trivial arithmetic), keep fx/pi as fixed reference arms in the referee, and only route per task once Hermes is within ~20% of fx on the same set. Routing earlier just hides the gap.

4. HARDWARE PERSONALIZATION AND STRESS

Key design move: for hardware questions, remove the model. Replay the recorded tool-call sequences from the 1,795 locate runs (and the terminal tail) deterministically on each hardware configuration. That isolates disk/cgroup/cache effects from provider noise, costs no tokens, and runs on both hosts. Experiments, ranked by expected information per hour:
  1. cgroup quota 2 vs 8 vs unlimited on groot, trace replay. Predict: p50 flat, p90 of terminal and any batched turn drops substantially. If flat everywhere, the quota is fine and stays.
  2. sessions.db and Hermes state DBs: SATA btrfs CoW vs NVMe xfs vs btrfs+nodatacow. Measure ctx search and session_search under each. Predict 46 s → <5 s.
  3. Cold vs warm page cache on /mnt/zer0models worktrees: drop_caches, then replay a search burst. If cold-first-search is >5 s, a vmtouch pre-warm of active worktrees is a one-line win.
  4. OOM/memory-PSI correlation: join tool failures with PSI avg10 (start the E1 host recorder; metrics-server is useless for this — a 5 s node_exporter/psi logger into sqlite is enough).
  5. GPU co-tenancy: router TTFT with vs without llama.cpp load, since the router sits on every routed turn.
  6. groot vs mbp: same replay set on both; hand placement rules to Kerdoios.
Validity: A/A first on every configuration, interleaved ABBA, ≥20 pairs, PSI (cpu/mem/io) logged per run as covariates, runs with co-tenant load above a threshold flagged and excluded, declared cache state per arm (cold or pre-warmed, never "whatever"), paired bootstrap on medians AND p90.

5. TOP 5 ATTACK SURFACES

  1. Locate turn count (batching + locator index + toolkit card). Predicted: Locate-set median wall −30–50%, turns −40% (Inference from 53% single-call turns, 67% empty→search chains). Falsifier: ABBA on 60 dev tasks shows <3% on both. First experiment: prompt-only change ("batch independent reads; after an empty result, change scope or query the index, never re-grep") plus exposing the locator index as one tool; one afternoon. Owner: z0intelligence (card/index), hermes-agent (prompt).
  2. Per-turn prompt size and cold start. Predicted: turn p50 8.1→~6 s (Inference from z0evals#110 TTFT deltas). Falsifier: turn-time vs input-token regression on the 130k turns is flat. First experiment: that regression, today, zero cost. Owner: Tokenomics (measurement), hermes-agent (catalog→retrieval).
  3. Waiting. Predicted: −5–8% total wall. Falsifier: classifying the 4,816 sleep calls shows most waits were needed (process not ready at wake). First experiment: that classification from existing data; then shadow-mode wait law via the laws registry. Owner: hermes-agent + laws.
  4. State-DB IO (ctx search 46 s, session/memory searches). Small hours, but it is the stall pattern that makes agents abandon the right tool, and unified memory sits on top of it. Predicted 46→<5 s. Falsifier: iostat/strace show CPU-bound Python, not IO. First experiment: one strace'd call, then copy DB to NVMe. Owner: ctx / .files.
  5. groot cgroup quota + OOM kills. Predicted: retry turns from kills eliminated; p90 terminal down. Falsifier: kill count in 45 days is negligible and replay shows no quota sensitivity. First experiment: count Killed/137 results; replay under quota 2 vs 8. Owner: .files, Kerdoios.

6. MEASUREMENT-VALIDITY THREATS

  - Subagent double counting (parent tool wall ⊇ child run) inflates terminal/process hours; split parent vs child before Amdahl claims.
  - Multi-call turns share one parent timestamp; summing per-tool wall overstates.
  - Timing proxy includes provider queue; Astra vs Opus differences may be nothing to do with Hermes.
  - Profile measured under nproc=2 and OOM-guard; tails are environment-shaped.
  - Locate set mined from long runs is survivorship-biased toward hard/ambiguous queries; held-out set should also sample random short locates, or you'll optimize pathology and regress the common case.
  - "First correct evidence" oracle comes from where the historical run eventually found it, which may itself be wrong or non-unique.
  - Power: 40 held-out tasks with ±30% median noise detects roughly ≥15% effects; a 3% KEEP threshold is below your resolution unless A/A half-width is measured first and is actually that small.
  - ABBA with provider prompt caching and page cache: second arm is warmer. Randomize order per pair and declare cache state.
  - Pass-rate drop must be a veto, not a secondary.

7. DROP / POSTPONE / WRONG

Drop: grep→rg, CLI cold start, Rust rewrites (already null). Postpone: speculative tool execution (no gain at 0.3 s tools); E0 cross-harness timing beyond keeping fx as a reference arm (#110 already answered it). Ship without a referee: F scoped search defaults and timeouts (fd p90 300 s is a bug, not a hypothesis), D ctx DB placement. Wrong in the draft: turns as tie-break (make co-primary); no pass-rate gate; single-model Hermes oneshots cannot see the 50% of cost that is subagents — add one delegated task class; KEEP threshold declared before A/A half-width is known.

IF YOU ONLY DO ONE THING THIS WEEK
  1. Run the A/A on the Locate set and publish the half-width before any KEEP decision.
  2. Regress turn time on input tokens over the 130k turns; it decides whether prompt diet is track #1 or #2.
  3. Classify the 4,816 sleeps and count OOM-killed tools from existing data.
  4. Build the model-free trace replay and run cgroup 2-vs-8 and DB-on-NVMe on it.
  5. Ship scoped-search defaults and timeouts as plain code; verify fd p90 collapses in next week's profile.

=================== ROUND-1 OPENING: astra
Inference — Council seat: gpt-6-astra. Optimize verified progress along a task’s critical path, not tool speed in isolation. Keep AutoKernel’s keep/revert discipline, but use several workload tracks with a Pareto table rather than one universal score.

1. Reframe: a policy over execution paths

Inference — The optimization unit should be a verified task episode: request → evidence acquisition → action, where applicable → verification. Represent execution as a dependency graph with model, tool, queue, approval, and background-job spans. A faster node matters only when it shortens the critical path or releases constrained capacity.

Inference — Maintain Locate, Execute/Verify, and sustained-service tracks. Report correctness, completion rate, p50/p90 latency, monetary/token cost, and host interference. Within each track, choose a primary latency objective subject to correctness and resource guardrails. “First correct evidence” is useful for Locate, but can reward premature stopping on tasks requiring comprehensive evidence.

Inference — “Which tool where?” belongs in four layers:
  • Code defaults: safe scope, bounded output, deadlines, cancellation, index-freshness checks.
  • Compact capability cards: task applicability, prerequisites, output contracts, examples.
  • Router: select tool sequences and fallbacks using task, host, load, freshness, and uncertainty.
  • Harness/placement policy: select execution environment subject to capability and permission parity.

Inference — Use deterministic defaults first; compare the existing kNN router against them before training. The router must earn back its inference and dispatch latency. Preserve the existing hub ownership split; do not create another skill registry.

2. Amdahl diagnosis

Measured — Model turns account for 606 h; terminal accounts for 361 h of the 579 h tool proxy. Search/read sequences account for 282 h. These are overlapping classifications, not independent savings pools.

Inference — The strongest opportunity is removing unnecessary decisions and synchronization, not accelerating already-fast searches. Eliminating a model round trip can outweigh many milliseconds saved inside rg. However, model medians across different workloads do not establish causal model-speed rankings.

Measured — Sleep-containing commands account for 105 h, process_manage waits 71 h, and clarification 19 h. The supplied aggregates do not identify how much is avoidable.

Inference — Waiting for real work is not waste by definition. Replacing sleep with asynchronous completion improves responsiveness and may enable overlap; it does not necessarily shorten the underlying job. Measure completion-detection lag separately from job runtime.

Measured — 53% of results come from single-call turns. Long search runs and immediate retries after near-empty results are common.

Inference — Neither statistic measures parallelizable work. Label a sample for dependencies and whether all query inputs were already available. Parallelize those reads; do not issue speculative searches merely to improve a batching metric.

Inference — The profile cannot distinguish terminal subprocess work, filesystem stalls, throttling, queueing, serialization, output handling, or provider prefill. Therefore it cannot yet support a numerical system-wide Amdahl speedup bound.

3. Relevant frontier and harness choice

Source — Anthropic’s advanced tool-use work combines on-demand tool discovery, programmatic orchestration, and invocation examples. It explicitly notes that discovery adds latency and is not universally beneficial:
https://www.anthropic.com/engineering/advanced-tool-use

Inference — Apply that mechanism to the existing hub: keep frequent capabilities resident; retrieve uncommon ones. Compare total task latency, not catalog token reduction alone. A skill lookup that saves prompt tokens but adds another expensive turn may lose.

Source — Code execution with MCP demonstrates filtering and composing tool outputs outside model context, while acknowledging sandboxing and operational overhead:
https://www.anthropic.com/engineering/code-execution-with-mcp

Inference — This is particularly relevant to trace analysis: aggregate locally and return bounded evidence with provenance. Preserve tool-level permissions and spans inside compound execution; otherwise faster orchestration becomes less auditable.

Source — Recursive Language Models externalize long input into a programmable environment and recursively inspect portions of it:
https://arxiv.org/abs/2512.24601v1

Inference — Build on the existing H0×Z×R×S ablations. RLM is a candidate for large-history reasoning, not a universal accelerator for locating a file. Include recursion budgets, tail latency, and evidence-loss tests; distinguish the paper’s mechanism from local State Packet implementations.

Source — ToolOrchestra trains small orchestrators using outcome-, efficiency-, and preference-aware rewards:
https://arxiv.org/abs/2511.21689

Inference — Extend z0intelligence#20 and the existing shadow-network approach, rather than launching a competing router project. Optimize verified downstream outcomes, not route-label agreement alone. Offline traces lack outcomes for unchosen tools, so shadow scoring needs controlled execution trials.

Measured — Warm Hermes ACP achieved 1.83 s TTFT; fx-base and pi were faster on the supplied task suites. Those suites were small and uniformly successful.

Inference — Route bounded, capability-matched tasks experimentally to pi/fx while porting demonstrated mechanisms into Hermes: resident workers, smaller active catalogs, and fewer unnecessary turns. Do not infer a wholesale migration mandate. For speculative execution, start only with bounded read-only work, cancellation, freshness validation, and explicit resource budgets.

4. Hardware personalization and stress

Inference — First instrument cgroup cpu.max/cpu.stat, effective cpusets, memory events, PSI, process RSS/faults, disk activity, GPU utilization/VRAM, and provider spans. Treat “nproc=2” as motivation to inspect limits, not a complete explanation of CPU capacity.

Inference — Run a staged experiment, not a full Cartesian matrix:
  • Replay actual read-only DB/index queries on consistent snapshots across NVMe and SATA, warm and cold cache.
  • Compare current CPU allocation with one safely increased allocation, subject to host headroom.
  • Repeat under controlled memory pressure, observing reclaim, zram, faults, and OOMs.
  • Compare local-router idle versus concurrent inference.
  • Compare eligible groot/mbp work, including transfer, queue, and startup time; retain the fleet-build rule.

Inference — Disk location currently confounds medium, filesystem, and workload. A cross-disk comparison estimates a placement bundle, not “btrfs cost.” Do not move live state databases or globally drop caches on the owner’s active host.

Inference — Separate quiet causal benchmarks from realistic co-tenant stress runs. Randomize within time/load blocks; run A/A in every stress regime; repeat across days. Retain killed and timed-out runs as failures. Record owner workload disruption as an outcome, not noise to discard.

5. Ranked attack surfaces

Inference — The following are experiment-planning hypotheses, not measured effects; ranges are conditional and non-additive.

1) Remove search decision loops.
Inference — Predicted effect: 15–40% lower Locate median, or 1–3 fewer model turns on affected tasks. First experiment: 20 development episodes, current policy versus a compact toolkit card with explicit scope and empty-result fallback; three paired repeats. Falsifier: no turn reduction or degraded evidence correctness. Owners: z0intelligence and z0evals; skill text in kvnloo-skills.

2) Remove harness/context overhead.
Inference — Predicted effect: 2–8 s lower cold TTFT on affected configurations; 10–25% lower short-task wall time. First experiment: extend harness-speed-v1 with matched warm/cold lifecycle and catalog settings on ten representative tasks. Falsifier: benefit disappears after lifecycle/capability matching or quality regresses. Owners: hermes-agent, z0evals.

3) Dependency-aware batching and completion notification.
Inference — Predicted effect: 10–30% lower latency on episodes with independent reads or delayed job detection; near-zero on inherently serial episodes. First experiment: label 30 traces, then replay ten eligible workflows with bounded concurrency and completion events. Falsifier: no critical-path reduction, or higher resource contention/retries. Owners: hermes-agent, oss-factory, z0evals.

4) Scoped search and locator freshness.
Inference — Predicted effect: 30–80% lower p90 on pathological search episodes; little median improvement on already-scoped searches. First experiment: replay 20 timeout/empty-result cases using scope defaults, explicit index freshness, and one bounded fallback. Falsifier: relevant evidence becomes inaccessible or escalation still repeats. Owners: z0intelligence, hermes-agent; laws registry for shadow decisions.

5) Pressure-aware placement.
Inference — Predicted effect: 10–30% lower p90 on demonstrably pressure-bound episodes; near-zero on provider-bound work. First experiment: paired read-only replays under baseline versus isolated low-pressure conditions with throttling/fault spans. Falsifier: latency does not covary with pressure or improve under intervention. Owners: Kerdoios, .files, Tokenomics, z0evals.

6. Validity threats and draft corrections

Inference — Parent-assistant-to-result timing can include queueing and earlier sibling execution; summed sibling durations can double-count elapsed time. Session/subagent overlap further separates aggregate agent-hours from owner waiting time. Preserve the historical proxy, but name it as such.

Inference — Locate cases mined only from long runs are a tail-remediation benchmark, not a deployment sample. Add ordinary successes, empty-result cases, and tasks whose answer is absent. Split by repository/task family and time to reduce leakage; freeze corpus snapshots and forbid locator memory from ingesting held-out answers.

Inference — Fresh oneshots systematically emphasize cold-start costs and omit valuable persistent state. Evaluate both cold and resident lifecycles. Randomize ABBA/BAAB ordering, use identical starting snapshots, and account for provider/prompt-cache carryover.

Inference — Bootstrap by task or task family, not individual correlated turns. Forty held-out cases support a pilot, not repeated unrestricted keep/revert decisions. Keep that set sealed until promotion; rotate future confirmation sets.

Inference — KEEP below 1−max(3%, A/A half-width) is insufficient alone: it conflates noise calibration with confidence in treatment benefit. Require a prespecified effect estimate, uncertainty criterion, correctness non-inferiority, and tail/resource guardrails. Include failures in the score rather than reporting success-only latency.

7. Drop or postpone

Inference — Postpone universal router training, broad Rust rewrites, whole-fleet tuning, and production DB relocation. Move the minimal E1 recorder before hardware claims; run D ctx latency as a bounded diagnostic, not the centerpiece.

Inference — Revise the declared null: grep→rg swaps may be low priority, but CLI cold start is not generally null given the supplied harness evidence. The 0.17 s floor is contextual, not a physical constant. Replace “sleep>30 s” denial with shadow classification of unnecessary polling versus legitimate waiting.

Inference — This week: instrument one task’s complete critical path, including provider and host spans.
Inference — Freeze a stratified development set and a genuinely sealed confirmation set.
Inference — Test one compact Locate policy against baseline with randomized paired repeats.
Inference — Add one resident-Hermes versus pi/fx comparison with capability parity.
Inference — Promote only verified improvements; publish failures, tails, costs, and host interference.

=================== ROUND-1 OPENING: sol
gpt-6.1-sol — independent opening

1. Reframe: optimize progress, not tool speed

[Inference] The unit of optimization should be a verified task episode, represented as a dependency graph: model decisions, tool executions, waits, evidence acquisition, and verification. Optimize its critical path, not the sum of tool durations. A faster lookup that adds another model turn can lose; a slower lookup that resolves the question can win.

[Inference] Use several tracks with a shared Pareto table, not one system-wide scalar:

  Locate: time to sufficient, verified evidence.
  Execute: time to correctly completed and verified work.
  Resume: time to recover the right state without repeating work.
  Fleet: successful work per resource budget, with interactive latency constraints.

[Inference] Report success rate, median/p90 elapsed time, model expenditure, owner interventions, and resource consumption. A scalar is useful inside a frozen track after correctness and safety constraints—not for trading away correctness or comparing unrelated workloads.

[Inference] “Which tool when?” belongs at four layers:

  Code defaults: enforce scope, bounded output, cancellation, safe concurrency.
  Capability/skill retrieval: expose contracts, examples, prerequisites, fallback paths.
  Router: select among genuinely substitutable tools, models, harnesses, and hosts.
  Prompt: express goals and exceptions, rather than enumerate every operational rule.

[Inference] Build on the existing hub decision and shadow-router portfolio. Begin with explicit rules and the existing kNN baseline; require any SLM to beat them on end-to-end task outcomes including routing overhead. Historical tool choice is not ground-truth optimal choice.

2. Amdahl diagnosis

[Measured] Model-turn time and tool timing proxies are both substantial: 606 h and 579 h. Search/read turns account for 282 h; terminal accounts for 361 h. Thus neither “only optimize inference” nor “only optimize tools” follows from this profile.

[Inference] Eliminating a search round can remove both model latency and tool latency. That is a more promising lever than making an already subsecond search engine modestly faster.

[Measured] Native warm-cache commands take milliseconds, whereas harness-level search medians are hundreds of milliseconds. Conversely, unscoped searches have tails measured in tens or hundreds of seconds. This supports targeting avoidable rounds and pathological scope before native-engine substitutions.

[Inference] These are accumulated activity-time proxies, not a partition of owner-visible elapsed time. Parallel sibling tools can each inherit the same assistant timestamp; subagents can overlap their parent. An Amdahl estimate based directly on these totals would overstate obtainable savings.

[Measured] Sleep-containing commands account for 105 h, but the profile does not establish which waits were unnecessary.

[Inference] Async completion can save blocked interaction and polling rounds without shortening the underlying build. Separate “work finished sooner” from “agent remained available sooner.”

[Inference] Terminal hides command class, CPU throttling, IO, locks, network, queueing, and memory stalls. Model-turn time similarly does not isolate prefill, decode, provider queueing, prompt-cache misses, or harness preparation. The CPU quota and memory-pressure observations are hypotheses to test, not explanations already established.

3. Frontier: borrow mechanisms, retain attribution

[Source] Anthropic’s advanced-tool-use work combines deferred tool discovery with programmatic orchestration and filtering of intermediate results:
https://www.anthropic.com/engineering/advanced-tool-use

[Inference] This is directly relevant to the 400-skill environment: retain a small always-available capability surface and retrieve the rest. Test discovery overhead and recall; “smaller prompt” alone is not success. Programmatic orchestration should replace deterministic repeated round-trips, not hide evidence needed for judgment.

[Source] RLM treats long input as an external environment that a model can inspect programmatically and recursively query:
https://arxiv.org/abs/2512.24601

[Inference] Continue the existing H0×Z×R×S program rather than launching another RLM project. Its relevant question is whether provenance-preserving external state reduces repeated orientation and context costs on Kevin’s traces—not whether recursion improves an unrelated benchmark. Charge child calls and state maintenance to the episode.

[Source] SPAgent reports search-agent latency improvements through adaptive speculative actions and load-aware scheduling; naive speculation can become slower under contention:
https://arxiv.org/abs/2511.20048

[Inference] Ordinary parallel reads should precede speculative execution. Later, speculate only authorized, bounded, read-only operations, with snapshot-aware cache keys and cancellation. A prediction is not permission; even read-only speculation can consume scarce IO or disclose data.

[Source] Anthropic’s long-running harness work emphasizes explicit progress artifacts and initialization rather than compaction alone:
https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents

[Source] GEPA uses trajectory reflection and Pareto selection to evolve prompts:
https://arxiv.org/abs/2507.19457

[Inference] These fit existing State Packets and the autoresearch loop: optimize small capability cards against failure trajectories, with explicit state recovery and untouched validation—not more permanent instruction text.

[Measured] PR #110 shows fx/pi advantages on the tested MBP tasks, but warm Hermes ACP has lower median TTFT than the listed cold alternatives.

[Inference] Route bounded tasks experimentally before migrating wholesale. Port demonstrated mechanisms—persistent workers, lean context, deterministic orchestration—into Hermes where capabilities matter. Compare deployment mode and capability parity, not harness names alone.

4. Hardware personalization and stress

[Inference] Prioritize three experiments:

  CPU quota: replay representative lookup/SQLite/agent workloads under the observed quota and a higher permitted quota; capture cpu.stat throttling.
  Memory: repeat under ordinary and bounded additional pressure; record PSI, major faults, swap/zram activity, OOM kills.
  Storage: use consistent disposable database/index snapshots on NVMe and SATA, with identical queries and explicit warm/first-touch conditions.

[Inference] Existing NVMe/xfs versus SATA/btrfs placements confound device and filesystem. Call that comparison a placement bundle unless filesystem is separately controlled. Do not relocate live state databases for the first experiment.

[Inference] Keep an isolated causal lane and a realistic co-tenant lane. Randomize paired AB/BA blocks across time; run A/A in each stress cell. Fix workload and concurrency, not just a vague “stress percentage.” Record actual pressure, router queue latency, GPU utilization/VRAM, host, and competing activity. Do not flush global caches on the owner’s active machine.

[Inference] Test GPU-router co-tenancy after establishing a CPU/kNN baseline: a smarter local decision model can lose through queueing or memory competition. Compare groot and mbp using permitted workloads and current fleet-build ownership; hardware names alone do not justify placement.

5. Ranked attack surfaces

[Inference] The following are testable priors, not forecasts from measured causal effects.

1. Eliminate redundant discovery and orientation.
   Effect: 15–30% lower median Locate latency on difficult episodes.
   First experiment: 20 development episodes, current policy versus a compact toolkit card plus scoped fallback; annotate repeated and avoidable rounds.
   Falsifier: no reduction in rounds/latency, or evidence quality regresses.
   Owner: z0intelligence; z0evals referee.

2. Reduce per-turn harness/context overhead.
   Effect: 2–8 s less cold startup; 5–15% lower multi-turn task latency.
   First experiment: 12 paired tasks, persistent versus cold workers and lean versus existing catalog, holding capabilities/model fixed.
   Falsifier: benefits disappear warm, or tool discovery failures erase savings.
   Owner: hermes-agent; z0intelligence adapters; z0evals.

3. Bound search tails and add safe parallelism.
   Effect: 20–50% lower p90 on pathological Locate episodes; 5–15% lower median where independent reads exist.
   First experiment: manually annotate dependency graphs for 20 episodes, then compare existing behavior with bounded scoped searches and at most two independent concurrent reads.
   Falsifier: few parallel opportunities, contention removes gains, or bounded search misses required evidence.
   Owner: harness tool implementations; z0intelligence orchestration; z0evals.

4. Replace polling with completion-driven continuation.
   Effect: remove one or more model rounds per polling-heavy episode; 10–60 s earlier agent availability in qualifying cases.
   First experiment: audit 30 sleep traces; replay 10 genuine polling cases with completion notification and identical underlying jobs.
   Falsifier: waits were required, polling already negligible, or completion is lost.
   Owner: hermes-agent/process lifecycle; oss-factory for queued jobs.

5. Relieve measured host contention.
   Effect: 10–30% lower p90 tool latency in pressure-qualified episodes; potentially no improvement when idle.
   First experiment: small CPU-quota × memory-pressure matrix, A/A first, before disk migration.
   Falsifier: throttling/faults do not track latency, or relief does not improve task outcomes.
   Owner: Tokenomics recorder; .files controls; Kerdoios placement.

6. Referee validity and draft corrections

[Inference] Keep Locate as a diagnostic track, not the system’s sole objective. Mining only long search runs selects known failures and invites regression-to-the-mean. Add ordinary short episodes, no-answer cases, stale-memory cases, and end-to-end completion checks.

[Inference] “First correct evidence” needs a frozen sufficiency rubric and canonical readback: finding a plausible path is not resolving the question. Include timeout/failure outcomes; reporting latency only among successful runs rewards fast failure.

[Inference] Split by repository/task family and originating session, not randomly by individual search run. Locator memory must not ingest held-out answers or post-task state. Fresh oneshots should receive reconstructable historical context; otherwise they test a different problem.

[Inference] ABBA helps drift but does not eliminate carryover from warmed caches, indexes, or provider caching. Randomize block direction and record cache conditions. Bootstrap at the paired task/session level, not individual tool calls.

[Inference] The KEEP rule needs a precisely defined ratio statistic, uncertainty on that statistic, correctness noninferiority, and a tail guardrail. An A/A half-width is a noise diagnostic, not protection against repeated adaptive testing. Reserve the held-out set for finalist confirmation.

[Inference] The draft’s “CLI cold start” null is too broad given PR #110. Native grep substitutions may be low-value; harness worker startup demonstrably deserves its own track. Treat the 0.17 s floor as workload-specific, not a physical constant.

7. Drop or postpone

[Inference] Postpone Rust rewrites, broad PC tuning, live database moves, new router training, and speculative execution until attribution improves. Keep ctx latency as a targeted reliability fix, not a presumed system-wide unlock. Replace blanket sleep denial with shadow classification of wait intent; legitimate backoff and readiness waits exist.

[Inference] If you only do one thing this week: build 20 provenance-complete episode timelines.
[Inference] Mark critical paths, independent reads, repeated orientation, and legitimate waits.
[Inference] Run A/A, then toolkit-card/scoped-search A/B on those development episodes.
[Inference] Collect CPU throttling, PSI, faults, queueing, and prompt/cache metadata alongside them.
[Inference] Keep only verified gains; preserve an untouched finalist holdout.
