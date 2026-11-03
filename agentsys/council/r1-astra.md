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
