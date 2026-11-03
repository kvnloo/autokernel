# Upper bound on batching: for consecutive single-call turns, is call N+1's argument text
# free of any distinctive token (>=6 chars) that first appeared in result N? (=> could have been issued together)
import sqlite3, json, re, time, collections as C
SINCE=time.time()-21*86400
RO={"read_file","search_files","web_search","web_extract","session_search","skill_view","vision_analyze"}
TOK=re.compile(r"[A-Za-z0-9_./\-]{6,}")
def ro(name,args):
    if name in RO: return True
    if name=="terminal":
        c=args.get("command","") if isinstance(args,dict) else ""
        return bool(re.match(r"\s*(cd \S+ ?(&&|;) ?)*(rg|grep|find|fd|ls|cat|head|tail|sed -n|wc|git (log|show|status|diff|grep)|gh (pr|issue) (view|list)|sqlite3)\b",c))
    return False
stats=C.Counter()
for db in ["/workspace/hermes-home/profiles/clean/state.db","/workspace/hermes-home/state.db"]:
    con=sqlite3.connect(f"file:{db}?mode=ro",uri=True)
    prev=None; sid0=None; pend=None
    for sid,role,tcs,content in con.execute("select session_id,role,tool_calls,substr(content,1,20000) from messages where timestamp>=? order by session_id,id",(SINCE,)):
        if sid!=sid0: sid0,prev,pend=sid,None,None
        if role=="assistant" and tcs:
            try: arr=json.loads(tcs)
            except Exception: arr=[]
            if len(arr)!=1: prev=None; pend=None; continue
            f=arr[0].get("function",{}); a=f.get("arguments") or ""
            try: aj=json.loads(a)
            except Exception: aj={}
            cur=(f.get("name"),a,ro(f.get("name"),aj))
            if prev and prev[2] and cur[2]:
                stats["ro_pairs"]+=1
                newtok=set(TOK.findall(prev[3]))-set(TOK.findall(prev[1]))
                if not (set(TOK.findall(a)) & newtok): stats["independent"]+=1
            pend=cur; prev=None
        elif role=="tool" and pend:
            prev=(pend[0],pend[1],pend[2],content or ""); pend=None
        elif role=="assistant": prev=None; pend=None
    con.close()
print(dict(stats), "independent share=%.0f%%"%(100*stats["independent"]/max(1,stats["ro_pairs"])))
