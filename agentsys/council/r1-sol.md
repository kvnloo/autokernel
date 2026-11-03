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
