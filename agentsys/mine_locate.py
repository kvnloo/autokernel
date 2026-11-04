#!/usr/bin/env python3
"""Mine Locate-task candidates from Hermes state.db files (read-only).

A candidate is a long discovery run (>= MIN_RUN consecutive search/read-only assistant turns)
whose final assistant reply cites a path that the run itself opened. The cited path is the
answer key. Candidates are written to a PRIVATE jsonl (trace-derived; never commit it to this
public repo); a human or the facilitator then writes a neutral question for each kept one.

usage: mine_locate.py OUT.jsonl STATE_DB [STATE_DB ...]   (env SINCE_DAYS, MIN_RUN)
"""
import json, os, re, sqlite3, sys, time

OUT, DBS = sys.argv[1], sys.argv[2:]
SINCE = time.time() - 86400 * float(os.environ.get("SINCE_DAYS", "30"))
MIN_RUN = int(os.environ.get("MIN_RUN", "4"))
READ_TOOLS = {"search_files", "read_file", "skill_view", "skills_list", "session_search",
              "z0_file_search", "web_search", "web_extract", "tool_search", "tool_describe"}
READ_HEADS = {"ls", "cat", "sed", "head", "tail", "rg", "grep", "find", "fd", "wc", "tree",
              "sqlite3", "git", "gh", "jq", "stat", "du", "readlink", "realpath", "file"}
PATH_RX = re.compile(r"(?<![\w.-])((?:/|~/)[\w.@+-]+(?:/[\w.@+-]+)+)")
HEAD_RX = re.compile(r"^\s*(?:cd\s+\S+\s*(?:&&|;)\s*)*(?:timeout\s+\S+\s+)?([\w./-]+)")


def calls_of(tool_calls):
    try:
        arr = json.loads(tool_calls or "[]")
    except Exception:
        return []
    out = []
    for c in arr:
        f = c.get("function", {})
        try:
            a = json.loads(f.get("arguments") or "{}")
        except Exception:
            a = {}
        out.append((f.get("name") or "", a if isinstance(a, dict) else {}))
    return out


def read_only(name, args):
    if name in READ_TOOLS:
        return True
    if name == "tool_call":
        return all((c.get("name") or "").startswith(("mcp__ctx__", "z0_file_search", "mcp__z0int"))
                   for c in args.get("calls") or [] if isinstance(c, dict))
    if name == "terminal" and not args.get("background"):
        m = HEAD_RX.match(args.get("command") or "")
        return bool(m) and os.path.basename(m.group(1)) in READ_HEADS
    return False


def opened_paths(name, args):
    if name == "read_file":
        return {args.get("path") or ""}
    if name == "search_files":
        return {args.get("path") or ""}
    if name == "terminal":
        return set(PATH_RX.findall(args.get("command") or ""))
    return set()


def mine(db, out):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.execute("pragma query_only=1")
    subagent = {sid for (sid,) in con.execute(
        "select id from sessions where parent_session_id is not null or source='subagent'")}
    rows = con.execute("select session_id, role, content, tool_calls, timestamp from messages "
                       "where timestamp >= ? order by session_id, id", (SINCE,))
    n = 0
    sid0, ask, run, run_paths, t0 = None, None, 0, set(), None
    for sid, role, content, tcs, ts in rows:
        if sid != sid0:
            sid0, ask, run, run_paths, t0 = sid, None, 0, set(), None
        if sid in subagent:
            continue
        if role == "user":
            text = (content or "").strip()
            if "[agentsys-episode]" in text:  # referee episodes are not owner work
                ask, run = None, 0
            elif text and not text.startswith(("[ASYNC", "[OUT-OF-BAND", "<t3_code")):
                ask, run, run_paths, t0 = text, 0, set(), ts
            elif text.startswith("<t3_code"):
                m = re.search(r"<user_request>\s*(.*?)\s*</user_request>", text, re.S)
                if m:
                    ask, run, run_paths, t0 = m.group(1), 0, set(), ts
            continue
        if role != "assistant":
            continue
        calls = calls_of(tcs)
        if calls and all(read_only(n_, a) for n_, a in calls):
            run += 1
            for n_, a in calls:
                run_paths |= {p for p in opened_paths(n_, a) if len(p) > 6}
            continue
        if calls:  # a mutating turn ends the discovery run without a verdict
            run, run_paths = 0, set()
            continue
        reply = content or ""
        if ask and run >= MIN_RUN and reply:
            cited = sorted(p for p in run_paths if p in reply)
            if cited:
                out.write(json.dumps({"db": db, "sid": sid, "ask": ask[:900], "run_turns": run,
                                      "secs": round(ts - (t0 or ts), 1), "cited": cited[:6],
                                      "reply_head": reply[:500]}) + "\n")
                n += 1
        run, run_paths = 0, set()
    con.close()
    return n


if __name__ == "__main__":
    with open(OUT, "w") as fh:
        os.chmod(OUT, 0o600)
        print(sum(mine(db, fh) for db in DBS), "candidates")
