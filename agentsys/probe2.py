import sqlite3, json, re, time, collections as C, random
SINCE=time.time()-45*86400
DBS=["/workspace/hermes-home/state.db"]+[f"/workspace/hermes-home/profiles/{p}/state.db" for p in ("clean","chiefstaff","intake","rippletern")]
kill=C.Counter(); tool_n=0
sleep_cls=C.Counter(); sleep_h=C.Counter()
RX={"ci/gh":r"gh (run|pr checks|pr view|api)|workflow","k8s":r"kubectl|k3s","build/test":r"pytest|cargo|npm|pnpm|make|gradle|tsc|uv run|fleet-build","logs/file":r"tail|journalctl|grep|cat |ls ","net/health":r"curl|wget|nc ","phone/adb":r"adb|phone","ssh":r"ssh ","model/train":r"nvidia-smi|train|llama|vllm"}
for db in DBS:
    con=sqlite3.connect(f"file:{db}?mode=ro",uri=True)
    for (c,) in con.execute("select substr(content,1,600) from messages where role='tool' and timestamp>=?",(SINCE,)):
        tool_n+=1
        if not c: continue
        if re.search(r'"exit_code": ?137|Exit code 137|signal 9: SIGKILL|\bKilled\b',c): kill["sigkill/137"]+=1
        if re.search(r'"exit_code": ?143|signal 15: SIGTERM|\bTerminated\b',c): kill["sigterm/143"]+=1
        if re.search(r'MemoryError|Cannot allocate memory|out of memory|OOM',c,re.I): kill["oom-text"]+=1
    con.close()
print("tool results",tool_n, dict(kill))
rows=[json.loads(l) for l in open("hermes_tools.jsonl") if '"has_sleep": true' in l]
for r in rows:
    cmd=r.get("cmd","")
    m=re.search(r"sleep\s+(\d+)",cmd); s=int(m.group(1)) if m else 0
    cls=next((k for k,v in RX.items() if re.search(v,cmd)),"other")
    sleep_cls[cls]+=1; sleep_h[cls]+=r["exec_s"]
    sleep_cls["_sum_declared_sleep_s"]+=s
print("sleep calls by target:",{k:(sleep_cls[k],round(sleep_h[k]/3600,1)) for k in sleep_h})
print("declared sleep seconds total h:",round(sleep_cls["_sum_declared_sleep_s"]/3600,1),"of exec h",round(sum(r['exec_s'] for r in rows)/3600,1))
bg=sum(1 for r in rows if r.get("bg")); print("sleep in background calls:",bg)
