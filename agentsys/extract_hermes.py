#!/usr/bin/env python3
"""Extract per-tool-call timing rows from Hermes state.db files (read-only).

Row = one tool result. Timing proxies (Hermes stores one REAL ts per message):
  exec_s  = tool.ts - parent_assistant.ts   (tool wall time, incl. queueing / approval)
  think_s = parent_assistant.ts - prev_msg.ts (model turn time that produced the call)
Output: JSONL to stdout-path argv[1].
"""
import json, re, sqlite3, sys, shlex, os

OUT = sys.argv[1]
DBS = sys.argv[2:]
SINCE = float(os.environ.get("SINCE_TS", "0"))

FIRST_TOK = re.compile(r"^\s*(?:cd\s+\S+\s*(?:&&|;)\s*)*(?:timeout\s+\S+\s+)?(?:sudo\s+)?([A-Za-z0-9_./~-]+)")

def term_head(cmd: str) -> str:
    m = FIRST_TOK.match(cmd or "")
    if not m:
        return "?"
    h = os.path.basename(m.group(1))
    return h

SEARCH_RX = re.compile(r"(?<![\w-])(rg|grep|egrep|fgrep|find|fd|ag|ugrep|git grep|locate|qmd|fff)(?![\w-])")

def search_tools(cmd):
    return sorted(set(m.group(1) for m in SEARCH_RX.finditer(cmd or "")))

n = 0
with open(OUT, "a") as out:
    for db in DBS:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.execute("pragma query_only=1")
        smeta = {sid:(m or "", src or "", pid is not None) for sid,m,src,pid in con.execute("select id, model, source, parent_session_id from sessions")}
        cur = con.execute(
            "select id, session_id, role, tool_calls, tool_call_id, tool_name, timestamp, length(content) "
            "from messages where timestamp >= ? order by session_id, id", (SINCE,))
        last_sid = None; prev_ts = None; parent = None; calls = {}
        for mid, sid, role, tcs, tcid, tname, ts, clen in cur:
            if sid != last_sid:
                last_sid, prev_ts, parent, calls = sid, None, None, {}
            if role == "assistant" and tcs:
                try:
                    arr = json.loads(tcs)
                except Exception:
                    arr = []
                calls = {}
                for c in arr:
                    f = c.get("function", {})
                    calls[c.get("id") or c.get("call_id")] = (f.get("name"), f.get("arguments") or "")
                parent = (ts, prev_ts, len(arr))
            elif role == "tool" and parent:
                name, args = calls.get(tcid, (tname, ""))
                try:
                    a = json.loads(args) if args else {}
                except Exception:
                    a = {}
                row = {
                    "db": os.path.basename(os.path.dirname(db)) or "default",
                    "sid": sid, "tool": name or tname,
                    "exec_s": round(ts - parent[0], 3),
                    "think_s": round(parent[0] - parent[1], 3) if parent[1] else None,
                    "fanout": parent[2], "result_chars": clen, "ts": ts,
                    "args_chars": len(args),
                }
                mm = smeta.get(sid, ("","",False)); row["model"], row["source"], row["child"] = mm
                if row["tool"] == "tool_call" and isinstance(a, dict):
                    cs = a.get("calls") or []
                    if cs and isinstance(cs, list) and isinstance(cs[0], dict): row["inner"] = cs[0].get("name"); row["inner_n"] = len(cs)
                if row["tool"] == "terminal" and isinstance(a, dict):
                    cmd = a.get("command", "")
                    row["head"] = term_head(cmd); row["cmd"] = cmd[:200]
                    row["search"] = search_tools(cmd)
                    row["bg"] = bool(a.get("background"))
                    row["has_sleep"] = bool(re.search(r"(^|[;&\s])sleep\s+\d", cmd))
                    row["ssh"] = "ssh " in cmd
                out.write(json.dumps(row) + "\n"); n += 1
            prev_ts = ts
        con.close()
print(n, "rows")
