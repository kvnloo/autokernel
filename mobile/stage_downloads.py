#!/usr/bin/env python3
"""Install only the bounded host-stage shortlist; retain receipts."""
import json, os, pathlib, subprocess, time
ROOT = pathlib.Path(__file__).resolve().parent
ZLV = pathlib.Path('/mnt/zer0models/models/zlv-tts')
OUT = ROOT / 'host-stage-evidence'
ids = ['firered_stream_vad_f32', 'pulsevad_2_1k_f32', 'moonshine_streaming_tiny_q8_0', 'moonshine_streaming_small_q8_0', 'kroko_asr_community_q8_0', 'sensevoice_small_q8', 'parakeet_tdt_q8_0', 'qwen3_asr_0_6b_q8_0', 'niagara_19m_f32']
sizes = json.loads((OUT/'download-sizes.json').read_text())
assert sum(x['size_bytes'] for x in sizes) <= 4_000_000_000
before = {str(p.relative_to(ZLV/'acpp-models')): p.stat().st_size for p in (ZLV/'acpp-models').rglob('*') if p.is_file()}
(OUT/'models-before.json').write_text(json.dumps(before, indent=2))
env = {k:v for k,v in os.environ.items() if k not in ('PYTHONHOME','PYTHONPATH')}
env['HF_HOME']='/mnt/zer0models/models/hf'
results=[]
for ident in ids:
    cmd=['nice','-n','10','python3','tools/model_manager_v2.py','install',ident,'--models-root',str(ZLV/'acpp-models')]
    t=time.monotonic()
    with (OUT/f'install-{ident}.log').open('w') as f:
        p=subprocess.run(cmd,cwd=ZLV/'audio.cpp',env=env,stdout=f,stderr=subprocess.STDOUT)
    results.append({'id':ident,'command':cmd,'returncode':p.returncode,'wall_s':time.monotonic()-t})
    (OUT/'downloads.json').write_text(json.dumps(results,indent=2))
    print(ident,p.returncode,flush=True)
