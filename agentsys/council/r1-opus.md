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
