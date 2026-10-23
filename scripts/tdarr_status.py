#!/usr/bin/env python3
"""One-shot Tdarr status: progress, throughput, node load, and scope safety.

Written to be the single source for a scheduled progress report, so it answers the
questions that actually came up while running this:

  * what has completed, and how fast  -> jobs + per-file durations
  * is it still working               -> running encodes counted on the HOSTS
                                         (get-nodes reports workers:{} while eight
                                         ffmpeg processes are busy)
  * is it safe                        -> FOREIGN counts records whose path sits outside
                                         their library root. A scan with a parent path
                                         silently queues other libraries' media, which
                                         happened once and must never recur unnoticed.
  * is the window open                -> so a stalled night is visible immediately
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tdarr_kick import LIBS, crud  # noqa: E402

NODES = {"prox3": "root@192.168.214.131", "prox4": "root@192.168.214.132"}
LOAD_SCRIPT = r'''
read -r _ a b c d e f g _ < /proc/stat; t1=$((a+b+c+d+e+f+g))
sleep 3
read -r _ a2 b2 c2 d2 e2 f2 g2 _ < /proc/stat; t2=$((a2+b2+c2+d2+e2+f2+g2))
dt=$((t2-t1)); busy=$(( ((a2-a)+(b2-b)+(c2-c)+(f2-f)+(g2-g))*100/dt ))
printf "cpu=%s load=%s ffmpeg=%s mem=%s\n" "$busy" "$(cut -d' ' -f1 /proc/loadavg)" \
  "$(ps -eo comm | grep -c tdarr-ffmpeg)" "$(free -m | awk '/^Mem:/{print $3"M/"$2"M"}')"
'''


def node_load(host: str) -> str:
    try:
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", host, LOAD_SCRIPT],
                           capture_output=True, text=True, timeout=60)
        return r.stdout.strip() or "unreachable"
    except Exception as exc:                       # noqa: BLE001 - report, never crash
        return f"error: {exc}"


def snapshot() -> dict:
    docs = crud("FileJSONDB", "getAll")
    docs = docs if isinstance(docs, list) else list(docs.values())
    jobs = crud("JobsJSONDB", "getAll")
    jobs = jobs if isinstance(jobs, list) else list(jobs.values())
    libs = crud("LibrarySettingsJSONDB", "getAll")
    libs = libs if isinstance(libs, list) else list(libs.values())

    states, foreign = {}, {}
    for name, db in LIBS.items():
        root = f"/media/videos/{name}/"
        mine = [d for d in docs if d.get("DB") == db]
        states[name] = dict(collections.Counter(
            str(d.get("TranscodeDecisionMaker")) for d in mine))
        foreign[name] = sum(1 for d in mine
                            if not str(d.get("file", "")).startswith(root))

    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    now = dt.datetime.now()
    block = f"{days[now.weekday()]}:{now.hour:02d}-{(now.hour + 1) % 24:02d}"
    hours, open_now = {}, {}
    for lib in libs:
        if lib.get("name") in LIBS:
            sched = lib.get("schedule") or []
            hours[lib["name"]] = sum(1 for h in sched if h.get("checked"))
            # Trust the schedule itself; a hardcoded 23-05 rule reports "closed"
            # while an opened hour block is happily transcoding.
            open_now[lib["name"]] = any(
                h.get("_id") == block and h.get("checked") for h in sched)
    return {"states": states, "jobs": jobs, "foreign": foreign,
            "hours": hours, "open_now": open_now, "block": block}


def report(args) -> str:
    now = dt.datetime.now()
    snap = snapshot()
    jobs = sorted(snap["jobs"], key=lambda j: j.get("start") or 0, reverse=True)
    ok = [j for j in jobs if j.get("status") == "Transcode success"]
    err = [j for j in jobs if j.get("status") == "Transcode error"]
    running = [j for j in jobs if not j.get("status")]

    out = [f"# Tdarr report — {now:%a %d %b %Y %H:%M} NZDT", ""]
    opening = [n for n, o in snap["open_now"].items() if o]
    out.append(f"**Window:** {'OPEN for ' + ', '.join(opening) if opening else 'closed'} "
               f"(block {snap['block']}; libraries scheduled "
               f"{sum(snap['hours'].values())} hours/week)")
    out.append("")

    out.append("## Libraries")
    for name, st in snap["states"].items():
        total = sum(st.values())
        done = st.get("Transcode success", 0)
        out.append(f"- **{name}**: {done}/{total} done, "
                   f"{st.get('Queued', 0)} queued, "
                   f"{st.get('Not required', 0)} not required, "
                   f"{st.get('Transcode error', 0)} errored")
    out.append("")

    out.append("## Jobs")
    live_err = sum(st.get("Transcode error", 0) for st in snap["states"].values())
    out.append(f"- success **{len(ok)}**, in flight **{len(running)}**; "
               f"historical error records **{len(err)}** "
               f"(live files currently errored: {live_err})")
    today = [j for j in ok if isinstance(j.get("start"), (int, float))
             and dt.datetime.fromtimestamp(j["start"] / 1000).date() == now.date()]
    if today:
        mins = [j.get("duration") / 60000 for j in today if isinstance(j.get("duration"), (int, float))]
        out.append(f"- completed today: {len(today)}"
                   + (f", total encode time {sum(mins)/60:.1f} h" if mins else ""))
    recent = ok[:args.recent]
    if recent:
        out.append("")
        out.append("| finished | took | file |")
        out.append("|---|---|---|")
        for j in recent:
            t = j.get("start")
            ts = dt.datetime.fromtimestamp(t / 1000).strftime("%d %b %H:%M") if isinstance(t, (int, float)) else "?"
            dur = j.get("duration")
            dm = f"{dur/60000:.0f} min" if isinstance(dur, (int, float)) else "-"
            out.append(f"| {ts} | {dm} | {Path(str(j.get('file'))).name[:58]} |")
    if err:
        out.append(f"  - most recent error record: "
                   f"{Path(str(err[0].get('file'))).name[:60]}")
    out.append("")

    out.append("## Nodes")
    out.append("")
    out.append("| host | cpu | load | encodes | mem |")
    out.append("|---|---|---|---|---|")
    for name, host in NODES.items():
        raw = node_load(host)
        kv = dict(part.split("=", 1) for part in raw.split() if "=" in part)
        out.append(f"| {name} | {kv.get('cpu','?')}% | {kv.get('load','?')} | "
                   f"{kv.get('ffmpeg','?')} | {kv.get('mem','?')} |")

    total_foreign = sum(snap["foreign"].values())
    out.append("")
    out.append("## Scope safety")
    if total_foreign:
        out.append(f"⚠️ **{total_foreign} FOREIGN records** — media outside its library "
                   f"root is queued. Run `scripts/tdarr_prune_foreign.py --apply`.")
    else:
        out.append("✅ 0 foreign records — every queued file sits inside its own library.")
    for name, n in snap["foreign"].items():
        out.append(f"- {name}: {n}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recent", type=int, default=8)
    args = ap.parse_args()
    print(report(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
