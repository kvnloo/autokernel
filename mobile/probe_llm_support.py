#!/usr/bin/env python3
"""Record actual negative text-generation probes, not invented LLM timings."""
import json, os, pathlib, shlex, subprocess, time
from host_stage_probe import ACPP, BIN, ENV, EVIDENCE, MODELS
cases=[
 ('qwen3-text','gen','qwen3',MODELS/'Qwen3-TTS-12Hz-0.6B-Base-GGUF'),
 ('lfm3-text','gen','lfm3_audio',MODELS/'LFM3-Audio-2B-GGUF/LFM3-Audio-2B-Q4_0.gguf'),
 ('llm-task','llm','lfm3_audio',MODELS/'LFM3-Audio-2B-GGUF/LFM3-Audio-2B-Q4_0.gguf'),
]
records=[]
for ident,task,family,model in cases:
    args=['nice','-n','10',str(BIN),'--task',task,'--family',family,'--model',str(model),'--backend','cpu','--threads','4','--text','Please answer in one brief sentence.','--max-tokens','40','--log']
    t=time.monotonic_ns(); p=subprocess.run(args,cwd=ACPP,env=ENV,capture_output=True,text=True,timeout=30)
    path=EVIDENCE/(ident+'-unsupported.log'); path.write_text(p.stdout+p.stderr)
    records.append({'id':ident,'command':shlex.join(args),'cwd':str(ACPP),'returncode':p.returncode,'proc_ms':(time.monotonic_ns()-t)/1e6,'stdout':p.stdout,'stderr':p.stderr,'log':str(path.name),'interpretation':'Negative capability probe only; not a 40-input-token measurement. max-tokens=40 is an output cap, not a prompt token count.'})
    assert p.returncode != 0
(EVIDENCE/'llm-blockers.json').write_text(json.dumps(records,indent=2)+'\n')
print(json.dumps(records,indent=2))
