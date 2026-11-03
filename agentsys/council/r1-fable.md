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
