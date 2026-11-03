import json,collections as C,sys
K=('sid','tool','exec_s','think_s','result_chars','head','search','inner','ts')
rows=[{k:v for k,v in json.loads(l).items() if k in K} for l in open(sys.argv[1] if len(sys.argv)>1 else 'hermes_tools.jsonl')]
SEARCH={'search_files','read_file','skill_view','skills_list','session_search','web_search','tool_search','tool_describe'}
def is_search(r):
    if r['tool'] in SEARCH: return True
    if r['tool']=='tool_call' and (r.get('inner') or '').startswith(('mcp__ctx__','z0_file_search','session_search')): return True
    if r['tool']=='terminal' and (r.get('search') or r.get('head') in ('ls','cat','sqlite3','head','tail','sed','wc','tree','eza','bat')): return True
    return False
def q(xs,p): xs=sorted(xs); return xs[min(len(xs)-1,int(p*len(xs)))] if xs else 0
# group into turns: (sid, think_s) consecutive
by=C.defaultdict(list)
for r in rows: by[r['sid']].append(r)
runs=[]; empty_followups=0; empties=0; turns_total=0; search_turns=0
turn_cost_search=0; turn_cost_all=0
for sid,rs in by.items():
    rs.sort(key=lambda r:r['ts'])
    turns=[]; cur=None
    for r in rs:
        k=r['think_s']
        if cur and cur['k']==k: cur['rows'].append(r)
        else: cur={'k':k,'rows':[r]}; turns.append(cur)
    run=[]
    for t in turns:
        s=all(is_search(r) for r in t['rows'])
        cost=max(0,min(t['k'] or 0,3600))+max(0,max(r['exec_s'] for r in t['rows']))
        if cost>3600: cost=0
        turns_total+=1; turn_cost_all+=cost
        if s: search_turns+=1; turn_cost_search+=cost; run.append(cost)
        else:
            if run: runs.append(run); run=[]
    if run: runs.append(run)
    # empty search followed by search
    for i,r in enumerate(rs[:-1]):
        if is_search(r) and r['result_chars']<120 and r['tool']!='read_file':
            empties+=1
            if is_search(rs[i+1]): empty_followups+=1
L=[len(r) for r in runs]; T=[sum(r) for r in runs]
print(f"turns={turns_total} pure-search/read turns={search_turns} ({100*search_turns/turns_total:.0f}%), their wall share={100*turn_cost_search/turn_cost_all:.0f}% ({turn_cost_search/3600:.0f}h of {turn_cost_all/3600:.0f}h)")
print(f"search runs: n={len(runs)} len p50={q(L,.5)} p90={q(L,.9)} p99={q(L,.99)}")
long=[r for r in runs if len(r)>=5]; print(f"runs>=5 turns: n={len(long)} hours={sum(map(sum,long))/3600:.0f}  (>=10: n={sum(len(r)>=10 for r in runs)}, h={sum(sum(r) for r in runs if len(r)>=10)/3600:.0f})")
print(f"near-empty search results={empties}, followed by another search={empty_followups}")
