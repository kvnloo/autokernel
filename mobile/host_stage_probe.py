#!/usr/bin/env python3
"""Host-only voice-stage smoke/WER runner; never imports the phone referee."""
import argparse, collections, datetime, hashlib, json, os, pathlib, re, selectors, shlex, statistics, subprocess, time, wave
ROOT=pathlib.Path(__file__).resolve().parent
EVIDENCE=ROOT/'host-stage-evidence'
ZLV=pathlib.Path('/mnt/zer0models/models/zlv-tts')
ACPP=ZLV/'audio.cpp'
MODELS=ZLV/'acpp-models'
BIN=ACPP/'build/linux-cpu/bin/audiocpp_cli'
ENV={**os.environ,'OMP_NUM_THREADS':'4','OPENBLAS_NUM_THREADS':'4','MKL_NUM_THREADS':'4','BLIS_NUM_THREADS':'4'}
# Every listed CLI recipe is executed. Unsupported LLM probes are separate.
RECIPES=[
 ('vad','earshot','earshot_vad','earshot_vad','streaming',[]),
 ('vad','silero-offline','silero_vad','silero_vad','offline',[]),
 ('vad','marblenet','marblenet_vad','marblenet_vad','offline',[]),
 ('vad','firered-stream','firered_vad','FireRedVAD-GGUF/firered-stream-vad-f32.gguf','streaming',[]),
 ('vad','pulsevad','pulsevad','PulseVAD-GGUF/pulsevad-2.1k-f32.gguf','offline',[]),
 ('turn','smart-turn','smart_turn','Smart-Turn-v3-GGUF/smart-turn-v3.2-f32.gguf','offline',[]),
 ('asr','moonshine-tiny','moonshine_asr','Moonshine-Streaming-GGUF/moonshine-streaming-tiny-q8_0.gguf','streaming',[]),
 ('asr','moonshine-small','moonshine_asr','Moonshine-Streaming-GGUF/moonshine-streaming-small-q8_0.gguf','streaming',[]),
 ('asr','kroko','kroko_asr','Kroko-ASR-GGUF/kroko-en-community-64-l-q8_0.gguf','streaming',['--language','en']),
 ('asr','sensevoice','sense_asr','SenseVoice-Small-GGUF/sensevoice-small-q8-audiocpp-v1.gguf','offline',['--language','en']),
 ('asr','parakeet','parakeet_tdt','Parakeet-TDT-0.6B-v3-GGUF/parakeet-tdt-0.6b-v3-q8_0.gguf','offline',[]),
 ('asr','qwen3-asr','qwen3_asr','Qwen3-ASR-0.6B-GGUF/qwen3-asr-0.6b-q8_0.gguf','offline',['--language','English','--max-tokens','128']),
 ('asr','niagara','niagara_asr','Niagara-ASR-GGUF/niagara-19m-batch.en-f32.gguf','offline',[]),
]

def recipe(row):
    stage,ident,family,model,mode,extra=row
    args=['--task',stage,'--family',family,'--model','{M}/'+model,'--backend','cpu','--threads','4','--mode',mode,'--audio','{IN}','--log','--load-progress','1']
    args+=['--text-out' if stage=='asr' else '--segments-out','{OUT}'] if stage!='turn' else []
    args+=extra
    return dict(stage=stage,id=ident,family=family,model=model,mode=mode,cli_args_template=args)

def words(text):
    return re.findall(r"[a-z0-9]+",text.lower().replace("'",''))

def distance(a,b):
    row=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        nxt=[i]
        for j,y in enumerate(b,1): nxt.append(min(nxt[-1]+1,row[j]+1,row[j-1]+(x!=y)))
        row=nxt
    return row[-1]

def execute(rec,item,tag,timeout=180):
    out=EVIDENCE/(tag+('.txt' if rec['stage']=='asr' else '.segments.json'))
    log=EVIDENCE/(tag+'.log')
    inp=str(MODELS/item['audio'])
    args=[v.replace('{M}',str(MODELS)).replace('{IN}',inp).replace('{OUT}',str(out)) for v in rec['cli_args_template']]
    cmd=['nice','-n','10',str(BIN),*args]
    start=time.monotonic_ns()
    p=subprocess.Popen(cmd,cwd=ACPP,env=ENV,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    assert p.stdout is not None
    sel=selectors.DefaultSelector(); sel.register(p.stdout,selectors.EVENT_READ)
    buf=b''; lines=[]; events=[]; timings=collections.defaultdict(list)
    def consume(data):
        line=data.decode('utf-8',errors='replace').rstrip('\r')
        elapsed=(time.monotonic_ns()-start)/1e6
        lines.append(line)
        m=re.match(r'^\[TIMING[^\]]*\]\s+(\S+)\s+([-+0-9.eE]+)$',line)
        if m:
            try: timings[m[1]].append(float(m[2]))
            except ValueError: pass
        if any(x in line for x in ['runtime.load.phase loaded','session.wall_ms']) or line.startswith(('partial_text=','partial_text_snapshot=','text_output=','custom_schema_output=','event=','segments_out=')):
            events.append({'elapsed_ms':elapsed,'line':line})
    timed_out=False
    while sel.get_map():
        if (time.monotonic_ns()-start)/1e9>timeout:
            p.kill(); timed_out=True
        for key,_ in sel.select(.1):
            data=os.read(key.fd,65536)
            if not data:
                sel.unregister(key.fileobj)
                continue
            buf+=data
            while b'\n' in buf:
                line,buf=buf.split(b'\n',1); consume(line)
    if buf:consume(buf)
    p.wait(); proc_ms=(time.monotonic_ns()-start)/1e6
    log.write_text('\n'.join(lines)+'\n')
    record={'recipe':rec['id'],'item':item['id'],'command':shlex.join(cmd),'cwd':str(ACPP),'env':{k:ENV[k] for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','BLIS_NUM_THREADS')},'returncode':p.returncode,'timed_out':timed_out,'proc_ms':proc_ms,'audio_s':item['duration_s'],'log':str(log.relative_to(ROOT)),'events':events,'timings':{k:{'count':len(v),'first':v[0],'sum':sum(v),'median':statistics.median(v),'min':min(v),'max':max(v)} for k,v in timings.items()}}
    wall=timings.get('session.wall_ms')
    record['session_wall_ms']=wall[0] if wall and len(wall)==1 else None
    record['session_wall_scope']='single_marker_not_assumed_total' if wall and len(wall)==1 else 'multiple_subrequest_or_chunk_markers' if wall else 'absent'
    record['process_rtf']=proc_ms/1000/item['duration_s']
    record['session_rtf']=wall[0]/1000/item['duration_s'] if wall and len(wall)==1 else None
    for name,prefixes in [('load_done_ms',('runtime.load.phase loaded',)),('first_partial_ms',('partial_text=','partial_text_snapshot=')),('final_result_ms',('text_output=','custom_schema_output=','segments_out='))]:
        found=[e['elapsed_ms'] for e in events if any((pfx in e['line'] if name=='load_done_ms' else e['line'].startswith(pfx)) for pfx in prefixes)]
        record[name]=found[0] if found else None
    if rec['stage']=='asr':
        hyp=out.read_text().strip() if out.exists() else ''
        ref=words(item['text']); h=words(hyp)
        record.update(reference=item['text'],hypothesis=hyp,word_errors=distance(ref,h),reference_words=len(ref),wer=distance(ref,h)/len(ref),valid_output=bool(hyp))
    elif rec['stage']=='turn':
        result=[json.loads(e['line'].split('=',1)[1]) for e in events if e['line'].startswith('custom_schema_output=')]
        record['turn_output']=result[-1] if result else None
        record['expected_end_of_turn']=item['expected_end_of_turn']
        record['valid_output']=bool(result)
    else:
        record['segments']=json.loads(out.read_text()) if out.exists() else None
        record['valid_output']=out.exists()
    with (EVIDENCE/'runs.jsonl').open('a') as f:f.write(json.dumps(record)+'\n')
    print(' '.join(str(x) for x in (tag,p.returncode,round(proc_ms,2),record.get('hypothesis',record.get('turn_output','')))).rstrip(),flush=True)
    return record

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--only',nargs='*'); ap.add_argument('--streaming-smoke',action='store_true'); ap.add_argument('--wait-model-seconds',type=int,default=0); a=ap.parse_args()
    items=json.loads((MODELS/'ref/asr/transcripts.json').read_text())['items']
    recs=[recipe(r) for r in RECIPES]
    if a.streaming_smoke:
        recs=[dict(r,id=r['id']+'-streaming',mode='streaming',cli_args_template=[('streaming' if x=='offline' else x) for x in r['cli_args_template']]) for r in recs if r['stage']=='asr' and r['mode']=='offline' and r['family']!='niagara_asr']
    for rec in recs:
        if a.only and rec['id'] not in a.only:continue
        deadline=time.monotonic()+a.wait_model_seconds
        while not (MODELS/rec['model']).exists() and time.monotonic()<deadline:
            time.sleep(5)
        if not (MODELS/rec['model']).exists():
            raise SystemExit('model missing: '+rec['model'])
        selected=items if rec['stage']=='asr' and not a.streaming_smoke else items[-2:]
        repeats=1 if rec['stage']=='asr' else 5
        for rep in range(repeats):
            for item in selected:
                execute(rec,item,f"{rec['id']}-{item['id']}-r{rep}")
    (EVIDENCE/('streaming-recipes.json' if a.streaming_smoke else 'recipes.json')).write_text(json.dumps(recs,indent=2)+'\n')
if __name__=='__main__':main()
