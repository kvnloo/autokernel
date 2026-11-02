#!/usr/bin/env python3
"""Generate deterministic CPU Kokoro voice-stage fixtures (no phone)."""
import hashlib, json, pathlib
import numpy as np
import soundfile as sf
import torch
from scipy.signal import resample_poly
from kokoro import KPipeline

torch.set_num_threads(4)
torch.set_num_interop_threads(4)
torch.manual_seed(7)
DEST=pathlib.Path('/mnt/zer0models/models/zlv-tts/acpp-models/ref/asr')
DEST.mkdir(parents=True,exist_ok=True)
# Split synthesis makes the two exact 800 ms mid-sentence pauses reproducible.
DATA=[
 ('asr01','af_heart',['Could you remind me to water the plants tonight?'],True),
 ('asr02','am_adam',['Please set a timer for five minutes.'],True),
 ('asr03','bf_emma',['What is the easiest way to cook brown rice?'],True),
 ('asr04','af_heart',['Turn the living room lights down a little.'],True),
 ('asr05','am_adam',['I think we should leave after lunch.'],True),
 ('asr06','bf_emma',['Can you help me find my blue notebook?'],True),
 ('asr07','af_heart',['No thanks, I already have enough coffee.'],True),
 ('asr08','am_adam',['Move my meeting with Alex to Friday afternoon.'],True),
 ('asr09','bf_emma',['Let me check the address before we go.'],True),
 ('asr10','af_heart',['That sounds good, but can we make it earlier?'],True),
 ('asr11','am_adam',['Before we leave,','please close the kitchen window.'],True),
 ('asr12','bf_emma',['So what I wanted','to ask was...'],False),
]
pipes={c:KPipeline(lang_code=c,device='cpu',repo_id='hexgrad/Kokoro-82M') for c in ('a','b')}
items=[]
for ident,voice,parts,complete in DATA:
    audio=[]
    for i,text in enumerate(parts):
        if i: audio.append(np.zeros(19200,dtype=np.float32))
        chunks=[r.audio.numpy() for r in pipes[voice[0]](text,voice=voice,speed=1)]
        assert chunks
        audio.append(np.concatenate(chunks))
    x=np.concatenate([np.zeros(2400,dtype=np.float32),*audio,np.zeros(7200,dtype=np.float32)])
    x=resample_poly(x,2,3).astype(np.float32)
    path=DEST/f'{ident}.wav'
    sf.write(path,x,16000,subtype='PCM_16')
    item={'id':ident,'audio':f'ref/asr/{ident}.wav','text':' '.join(parts),'voice':voice,'expected_end_of_turn':complete,'mid_sentence_pause_ms':800 if len(parts)>1 else 0,'sample_rate':16000,'channels':1,'format':'PCM_16','duration_s':len(x)/16000,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    items.append(item)
    print(ident,item['duration_s'],flush=True)
assert len(items)==12
manifest={'schema_version':1,'generator':'kokoro 0.9.4; hexgrad/Kokoro-82M; CPU 4 threads; seed 7; speed 1','notes':'Synthetic clean-speech smoke set, not natural speech or robust turn-quality validation. Terminal silence 300 ms; leading silence 100 ms. Pause-bearing complete/incomplete cases have 800 ms inserted between independently synthesized clauses.','items':items}
(DEST/'transcripts.json').write_text(json.dumps(manifest,indent=2)+'\n')
repo=pathlib.Path(__file__).resolve().parent
(repo/'host-stage-evidence'/'transcripts.json').write_text(json.dumps(manifest,indent=2)+'\n')
