#!/usr/bin/env python3
"""Aggregate only actual host receipts into the stage manifest and summary."""
import collections, hashlib, json, pathlib, statistics, subprocess, wave
from host_stage_probe import ROOT, EVIDENCE, MODELS, RECIPES, recipe, words, distance

STREAMING={
 'earshot':'Native 16 ms causal scoring; speech_start/speech_end events. No session.wall_ms in this streaming path.',
 'silero-offline':'Offline recipe validated. Streaming file input failed on the non-512-sample final chunk; raw failure retained.',
 'marblenet':'Offline only in this integration.',
 'firered-stream':'Causal stateful Stream-VAD; input transport chunks 2560 samples. session.wall_ms repeats per chunk, not whole clip.',
 'pulsevad':'Offline only; 200 ms analysis windows, 100 ms default hop. Not live streaming.',
 'smart-turn':'Offline classifier on last eight seconds, left-padded to eight seconds. Run after VAD pause, not continuously on arbitrary chunks.',
 'moonshine-tiny':'Accepts audio chunks but buffers ALL waveform and transcribes at finalize. No partials at this SHA; see src/models/moonshine_asr/session.cpp:282-361.',
 'moonshine-small':'Accepts audio chunks but buffers ALL waveform and transcribes at finalize. No partials at this SHA.',
 'kroko':'Native cache-aware stateful Zipformer/RNN-T streaming. partial_text deltas are flushed by CLI. Finalization adds 660 ms zero tail. File feed is unpaced.',
 'sensevoice':'Offline default auto chunking can invoke bundled Silero VAD and emit multiple session.wall_ms values. Also has buffered streaming; smoke recorded separately.',
 'parakeet':'Offline full-context primary recipe. Buffered streaming re-encodes bidirectional windows, not native cache-aware streaming.',
 'qwen3-asr':'Offline primary recipe; explicit English hint and 128-token transcript cap. Buffered streaming uses duration windows (30 seconds default). Not a general Qwen3 text LLM.',
 'niagara':'19M English batch checkpoint; offline only, no streaming or chunking.',
}

def stats(v):
    v=[x for x in v if x is not None]
    return {'n':len(v),'median':statistics.median(v),'min':min(v),'max':max(v)} if v else None

def summarize(rec,runs):
    rs=[r for r in runs if r['recipe']==rec['id']]
    assert rs,rec['id']
    ok=[r for r in rs if r['returncode']==0 and r['valid_output']]
    assert len(ok)==len(rs),(rec['id'],'failed outputs')
    expected=12 if rec['stage']=='asr' and not rec['id'].endswith('-streaming') else 2 if rec['stage']=='asr' else 10
    assert len(rs)==expected,(rec['id'],len(rs),expected)
    stage=rec['stage']
    post=[r['final_result_ms']-r['load_done_ms'] for r in rs if r['final_result_ms'] is not None and r['load_done_ms'] is not None]
    assert len(post)==len(rs)
    summary={'n':len(rs),'ok':len(ok),'process_ms':stats([r['proc_ms'] for r in rs]),'load_done_ms':stats([r['load_done_ms'] for r in rs]),'load_to_final_ms':stats(post),'post_load_rtf':sum(post)/1000/sum(r['audio_s'] for r in rs),'process_rtf':sum(r['proc_ms'] for r in rs)/1000/sum(r['audio_s'] for r in rs),'partial_runs':sum(r['first_partial_ms'] is not None for r in rs),'first_partial_ms':stats([r['first_partial_ms'] for r in rs]),'native_timings':{},'example_command':rs[0]['command'],'example_cwd':rs[0]['cwd'],'example_log':rs[0]['log'],'runs_index':'host-stage-evidence/runs-normalized.jsonl'}
    keys=sorted(set(k for r in rs for k in r['timings']))
    for k in keys:
        if not (k.endswith('_ms') or k.endswith('.ms') or k=='session.wall_ms'):continue
        # Repeated markers are per-chunk/subrequest, not assumed whole-session.
        summary['native_timings'][k]={'per_run_sum':stats([r['timings'][k]['sum'] for r in rs if k in r['timings']]),'marker_counts':sorted(set(r['timings'][k]['count'] for r in rs if k in r['timings']))}
    if stage=='asr':
        assert len({r['item'] for r in rs})==expected
        errors=sum(r['word_errors'] for r in rs); ref=sum(r['reference_words'] for r in rs)
        summary.update(wer=errors/ref,word_errors=errors,reference_words=ref,audio_seconds=sum(r['audio_s'] for r in rs),rtf=summary['post_load_rtf'],rtf_definition='sum(FINAL receipt ms - LOADED receipt ms) / sum(input WAV duration ms); includes request preparation, excludes model load; not a persistent warm session')
    if stage=='turn':
        predictions=[]
        for ident in sorted(set(r['item'] for r in rs)):
            group=[r for r in rs if r['item']==ident]
            out=group[0]['turn_output']; expect=group[0]['expected_end_of_turn']
            assert all(r['turn_output']==out for r in group)
            predictions.append({'item':ident,'expected_complete':expect,**out['data'],'correct':out['data']['complete']==expect})
        summary.update(predictions=predictions,accuracy=sum(p['correct'] for p in predictions)/len(predictions),quality_pass=all(p['correct'] for p in predictions))
    return summary

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def main():
    runs=[json.loads(x) for x in (EVIDENCE/'runs.jsonl').read_text().splitlines()]
    # Early probes inherited the referee's last-WALL assumption. Preserve the
    # append-only receipt file, but never expose those fields as whole-clip time.
    for r in runs:
        wall=r['timings'].get('session.wall_ms')
        single=bool(wall and wall['count']==1)
        r['session_wall_ms']=wall['first'] if single else None
        r['session_rtf']=wall['first']/1000/r['audio_s'] if single else None
        r['session_wall_scope']='single_marker_not_assumed_total' if single else 'multiple_subrequest_or_chunk_markers' if wall else 'absent'
    (EVIDENCE/'runs-normalized.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in runs))
    corpus=json.loads((MODELS/'ref/asr/transcripts.json').read_text())
    assert len(corpus['items'])==12 and len(list((MODELS/'ref/asr').glob('*.wav')))==12
    for it in corpus['items']:
        p=MODELS/it['audio']
        assert sha(p)==it['sha256']
        with wave.open(str(p)) as w:
            assert (w.getframerate(),w.getnchannels(),w.getsampwidth())==(16000,1,2)
    assert len(set(i['voice'] for i in corpus['items']))==3
    recs=[recipe(r) for r in RECIPES]
    secondary=json.loads((EVIDENCE/'streaming-recipes.json').read_text())
    stages={'vad':[],'turn':[],'asr':[],'llm':[]}
    for rec in recs:
        files=[MODELS/rec['model']] if (MODELS/rec['model']).is_file() else sorted(x for x in (MODELS/rec['model']).rglob('*') if x.is_file())
        entry={**rec,'status':'host_validated','input':{'sample_rate_hz':16000,'channels':1,'format':'WAV PCM signed 16-bit little-endian','file_streaming_feed':'unpaced; NOT real-time input cadence'},'output':{'vad':'segments JSON plus live events where supported','turn':'stdout custom_schema_output JSON, schema smart_turn.v1','asr':'stdout text_output and --text-out UTF-8 transcript'}[rec['stage']],'timing_markers':{'load_done':'runtime.load.phase loaded (enable --load-progress 1)','first_partial':'partial_text= or partial_text_snapshot=; first nonempty only; null if none emitted','final_result':'text_output=' if rec['stage']=='asr' else 'custom_schema_output=' if rec['stage']=='turn' else 'segments_out=','first_token':None,'wall':'session.wall_ms may be absent or repeated; see observed native_timings; never interpret last chunk as total'},'streaming':STREAMING[rec['id']],'assets':[{'path':str(p.relative_to(MODELS)),'bytes':p.stat().st_size,'sha256':sha(p)} for p in files],'host_results':summarize(rec,runs),'alternate_recipes':[]}
        for alt in secondary:
            if alt['id']==rec['id']+'-streaming':
                entry['alternate_recipes'].append({**alt,'host_results':summarize(alt,runs),'scope':'Only the two pause-bearing clips, not the 12-item WER set'})
        stages[rec['stage']].append(entry)
    payload_sizes=json.loads((EVIDENCE/'download-sizes.json').read_text())
    download_bytes=sum(p['size_bytes'] for p in payload_sizes)
    assert download_bytes<=4_000_000_000
    downloads=json.loads((EVIDENCE/'downloads.json').read_text())
    assert len(downloads)==len(payload_sizes) and all(d['returncode']==0 for d in downloads)
    failed=[{k:r[k] for k in ['recipe','item','returncode','command','log']} for r in runs if r['returncode']]
    blockers=[
      'No standalone text-to-text Qwen3/LFM2/LFM3 CLI route in audio.cpp a01aed4. LLM 40-input-token TTFT/tokens-per-second measurements are unavailable, not zero. See llm-blockers.json and loaders.json. No unsupported chat weights were downloaded.',
      'Smart Turn v3.2 predicts complete=true for the expected-incomplete synthetic clip asr12 (probability 0.924149751663208); this turn quality gate fails. Do not promote it on this eval set or tune/relabel the frozen fixtures to hide the failure.',
      'Silero streaming CLI fails for these file lengths: final audio chunk must contain exactly 512 samples. Offline recipe succeeds; production streaming needs correct framing/padding and original-length accounting.',
      'No phone or adb was used. All host numbers are smoke measurements on an Intel i9-10900KF under shared 2-CPU ancestor cgroup quota and changing system load; they do not predict S25 performance.',
    ]
    inventory=json.loads((EVIDENCE/'stage-inventory.json').read_text())
    value={'schema_version':1,'audio_cpp_commit':'a01aed4a15c1da59010e48bc15fb029ecb961975','benchmark_epoch':1,'base_commit':'1139ca41ed752038fe1300950bf8918963403e29','referee_modified':False,'models_root':str(MODELS),'host_binary':str(__import__('host_stage_probe').BIN),'host_run_cwd':str(__import__('host_stage_probe').ACPP),'host_environment':{'OMP_NUM_THREADS':'4','OPENBLAS_NUM_THREADS':'4','MKL_NUM_THREADS':'4','BLIS_NUM_THREADS':'4','nice':10},'runtime_requirements':'Run host CLI from the pinned audio.cpp checkout so current model_specs and bundled helper paths resolve. Phone binary needs AUDIOCPP_DEPLOYMENT_BUILD builtin contracts or an explicitly bundled full model_specs directory. SenseVoice default auto chunking also used bundled Silero weights at assets/framework/models/silero_vad. Host recipe validation is not Android runtime/package validation.','measurement_policy':{'asr':'One fresh CLI process per item per primary recipe, 12 items; no excluded warmups; default modes retained; models executed sequentially in recipe order, not interleaved. Alternate streaming modes: two clips, one process each. All raw results retained.','vad_turn':'Five repetitions each of complete and incomplete clips, sequential model order. Process-cold, OS/file/materialization caches uncontrolled; repeat ranges retained.','latency_clock':'monotonic_ns receipt timestamps in Python; source log ts fields are only whole-second human timestamps. Native log timing values also retained. Streaming file inputs are unpaced.','primary_rtf':'post-load-to-final receipt time / input audio duration; process-including-load RTF separately. Neither is a microphone streaming latency measurement.','wer':'Corpus summed Levenshtein word edit distance / reference words. Lowercase ASCII alphanumerics, strip apostrophes, discard punctuation; no number expansion (five vs 5 counts).','comparison_claim':'Exploratory shortlist qualification, not statistical performance ranking or phone-real-time certification.'},'evalset':{'path':'ref/asr/transcripts.json','n':12,'voices':sorted(set(i['voice'] for i in corpus['items'])),'manifest_sha256':sha(MODELS/'ref/asr/transcripts.json'),'pause_items':['asr11','asr12'],'generator_script':'mobile/generate_stage_evalset.py'},'downloads':{'new_package_payload_bytes':download_bytes,'downloads_gb_decimal':download_bytes/1e9,'limit_bytes':4_000_000_000,'packages':payload_sizes,'receipts':'host-stage-evidence/downloads.json','note':'Sum of nine selected package payload sizes, all installed successfully; excludes already installed Smart Turn/LFM3/Kokoro, local copies of bundled VAD weights and generated WAVs. HTTP retry/metadata overhead is not measured.'},'stages':stages,'unsupported_llm_candidates':[{'model':m,'status':'unsupported_in_audio_cpp','ttft_ms':None,'tok_s':None} for m in ['Qwen3-0.6B','Qwen3-1.7B','LFM2 text','LFM3 text']],'llm_negative_probes':json.loads((EVIDENCE/'llm-blockers.json').read_text()),'failed_experiments':failed,'catalog_inventory':inventory,'compiled_loader_inventory':'host-stage-evidence/loaders.json','blockers':blockers}
    (ROOT/'stages.json').write_text(json.dumps(value,indent=2)+'\n')
    print(json.dumps({'recipes':len(recs)+len(secondary),'runs':len(runs),'failed_runs':len(failed),'downloads_gb':download_bytes/1e9,'asr':[{'model':r['id'],'wer':r['host_results']['wer'],'rtf':r['host_results']['rtf']} for r in stages['asr']]},indent=2))
    lines=['# Host-qualified voice stages for S25 epoch 2','',f"Pinned audio.cpp `{value['audio_cpp_commit']}`; CPU-only Release CLI, `nice -n 10`, 4 requested threads. **No phone/adb used.** Epoch-1 `bench_s25.py` is unchanged.",'','## Results and what to bench first','', 'These are recipe/quality smoke measurements, not phone performance claims. RTF below is corpus post-load→final elapsed time divided by input duration; process RTF includes model loading. One process per ASR item, 12 clips/model; no warmup exclusions, uncontrolled page cache. Shared ancestor cgroup quota is **2 CPUs**, although each model requests four workers. VAD/turn use 5 repeats × 2 clips; ranges and exact commands are in `stages.json`.','', '| ASR primary recipe | WER | Post-load RTF | Process RTF | Median load→final ms | Partial runs |','|---|---:|---:|---:|---:|---:|']
    for r in stages['asr']:
        h=r['host_results']; lines.append(f"| {r['id']} ({r['mode']}) | {h['wer']:.4f} | {h['rtf']:.4f} | {h['process_rtf']:.4f} | {h['load_to_final_ms']['median']:.2f} | {h['partial_runs']}/{h['n']} |")
    lines+=['','### Ranked phone qualification order','', '1. **Earshot VAD + Moonshine Tiny Q8** for the first latency/footprint baseline; Tiny buffers the utterance until EOF at this SHA, so pair it with VAD/turn endpointing, not a claimed partial-transcript UX.', '2. **Kroko English Q8** when genuinely incremental text is required: stateful caches, real nonempty partials, and a 660 ms model finalization tail. Benchmark actual input cadence and endpoint flush latency.', '3. **SenseVoice Q8 and Moonshine Small Q8** as quality/size alternatives. SenseVoice default auto chunking includes Silero helper work; compare a separately frozen no-chunk configuration if isolating just ASR.', '4. **Qwen3-ASR 0.6B Q8 and Parakeet 0.6B Q8** for heavier quality alternatives. Keep loading and resident inference separate; Parakeet CPU weight upload was expensive in these process-per-item runs.', '5. **Niagara 19M F32** as an offline compact-batch reference. The published package size is not inferred from parameter count.', '6. **FireRed Stream-VAD, PulseVAD, MarbleNet and Silero offline** as VAD alternatives. Earshot/FireRed emit live events; PulseVAD/MarbleNet are offline here. Silero streaming failure is retained, not silently dropped.', '7. **Smart Turn v3.2** for timing only until quality is fixed/qualified: it fails the synthetic incomplete-turn gate. No promotion recommendation.', '8. **Standalone LLM: blocked** at this source revision. A separately pinned llama.cpp text runner or an upstream audio.cpp text route is required before an LLM or full cascade baseline. No LLM TTFT/tokens/s was fabricated.', '', 'The order is an engineering shortlist based on footprint, supported execution modes and this clean synthetic smoke set—not a statistically proven speed/accuracy ranking. No claim is made that any model is real-time on the S25 until measured there.', '', '## VAD and turn observations', '', '| Recipe | Mode | Median process ms | Min–max process ms | Median load→final ms |', '|---|---|---:|---:|---:|']
    for r in stages['vad']+stages['turn']:
        h=r['host_results']; p=h['process_ms']; lines.append(f"| {r['id']} | {r['mode']} | {p['median']:.3f} | {p['min']:.3f}–{p['max']:.3f} | {h['load_to_final_ms']['median']:.3f} |")
    lines+=['','Smart Turn outputs are deterministic across five repeats per clip:','']
    for p in stages['turn'][0]['host_results']['predictions']:
        lines.append(f"- `{p['item']}`: expected complete={p['expected_complete']}, actual={p['complete']}, p={p['probability']:.9f}, correct={p['correct']}.")
    lines+=['','The trailing-off clip is deliberately labelled incomplete. Kokoro synthesized its two clauses separately around an inserted 800 ms pause, which can create unnatural terminal prosody. This is a valid negative smoke result, not evidence of natural-conversation accuracy. Do not tune the 0.5 threshold on these two examples and call that general validation. VAD has no human frame labels in this set: detected segments prove output generation only, not F1.','', '## CLI and markers','', '`stages.json` is the machine-readable source of truth. Each validated entry contains a tokenized `cli_args_template`, model path relative to `acpp-models`, exact executed command/cwd/env, artifact hashes, observed native timing values, monotonic receipt-boundary timing, and raw log links. `{M}` is the models root, `{IN}` a 16 kHz mono PCM16 WAV, `{OUT}` the transcript/segments path (turn results are stdout JSON). Use templates with argv substitution, not string interpolation into an unquoted shell.','', 'All recipes use `--backend cpu --threads 4 --log --load-progress 1`; run from the pinned audio.cpp checkout on host. Android packaging needs builtin current contracts or bundled model specs and helper assets; these host results alone do not validate that bundle. The `turn` task works despite not appearing in the CLI help task enumeration.','', '- Load done: `[TRACE ...] runtime.load.phase loaded`. Upload progress is not a reliable replacement; some weights upload lazily.', '- ASR partial: first nonempty `partial_text=` delta or `partial_text_snapshot=` replacement. Final: `text_output=` plus nonempty `--text-out` artifact. Absence of partials is recorded, not assigned zero latency.', '- VAD streaming: `event=speech_start sample=N probability=P` and `event=speech_end ...`; final `segments_out=...` names the validated JSON.', '- Turn: `custom_schema_output={"schema":"smart_turn.v1","data":...}`; native frontend/encoder/classifier timings and `session.wall_ms` are recorded.', '- **session.wall_ms is not universally an entire utterance:** FireRed/Silero streaming emit it per chunk, SenseVoice can emit it for helper/chunk subrequests, while Earshot/Moonshine streaming omit it. The manifest retains marker counts and per-run sums without assuming nested sums are exclusive.', '- No first text-token marker or standalone text-generation path exists for the requested small LLMs. `gen` is primarily music/audio/video, not a generic LLM task. Negative probe return codes/logs are in `llm-blockers.json`.', '', '## Eval set and replay', '', 'External fixtures: `/mnt/zer0models/models/zlv-tts/acpp-models/ref/asr/`: exactly 12 English 16 kHz mono PCM16 WAVs plus `transcripts.json`. Three Kokoro 0.9.4 voices (`af_heart`, `am_adam`, `bf_emma`), four clips each. `asr11` is complete and `asr12` trails off; both contain an exact inserted 800 ms mid-sentence pause. Leading/trailing silence is 100/300 ms. The manifest records transcripts, expected turn labels, durations and hashes. A copy of the metadata (not the large models) is committed.','', '```sh', 'cd /mnt/zer0models/worktrees/ak-stages', 'nice -n 10 env -u PYTHONPATH -u PYTHONHOME HF_HOME=/mnt/zer0models/models/hf OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 MKL_NUM_THREADS=4 /mnt/zer0models/models/zlv-tts/.venv/bin/python mobile/generate_stage_evalset.py', '# Probe scripts append raw runs; use a new evidence directory or archive runs before a full replay.', 'env -u PYTHONPATH -u PYTHONHOME python3 mobile/host_stage_probe.py', 'env -u PYTHONPATH -u PYTHONHOME python3 mobile/host_stage_probe.py --streaming-smoke', 'env -u PYTHONPATH -u PYTHONHOME python3 mobile/probe_llm_support.py', 'env -u PYTHONPATH -u PYTHONHOME python3 mobile/summarize_stages.py', '```', '', 'Do not regenerate or append a new qualification batch over a frozen benchmark epoch without starting a new baseline. WER strips apostrophes/punctuation and lowercases; it does not expand numeric words, so `five` vs `5` counts as one substitution. Synthetic clean speech is not an adequate noise/accent/false-endpoint acceptance corpus.','', '## Inventory and budget', '', f"Installed nine new packages: **{download_bytes:,} payload bytes = {download_bytes/1e9:.6f} decimal GB**, below the 4 GB cap. Already-installed Smart Turn/Kokoro/LFM3 and repo-bundled VAD assets were reused; no standalone unsupported LLM weights were downloaded. Download receipts and package manifests retain provenance; HTTP retry overhead is unmeasured.", '', '`host-stage-evidence/stage-inventory.json` maps all model-spec ASR/VAD/turn families; `loaders.json` records compiled loader capabilities, including bundled Earshot/Silero/MarbleNet. The selected host recipes are a bounded CPU/English shortlist, **not** a claim that all larger, language-specific, offline-only, or experimental families were downloaded. Other available candidates include Canary 180M Flash, SAMSONE and audio8 ASR; large multilingual/diarizing/generative ASR was deferred under the budget. Inventory listings alone do not prove a model runs.', '', 'Source retrieval used the resident repository index; the Moonshine streaming-session symbol had no hit, so a bounded canonical `src/models/moonshine_asr/session.cpp` read established its EOF-buffering behavior.', '', '## Epoch-2 work', '', 'See [`proposals/bench-epoch2.md`](proposals/bench-epoch2.md) for exact minimal seams for ASR/turn/LLM/e2e routing, typed outputs, error/exit propagation, timing boundaries, asset sync and quality gates. This proposal was **not applied**. A complete end-to-end cascade is not yet qualified because standalone LLM support is absent.', '']
    (ROOT/'STAGES.md').write_text('\n'.join(lines))

if __name__=='__main__':main()
