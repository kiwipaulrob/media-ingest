#!/usr/bin/env python3
"""Overnight XviD->h264 batch (direct ffmpeg on the pool host, NOT Tdarr).

Per file: snapshot row -> encode (h264 veryfast CRF20, audio/sub copy) ->
decode+duration gate -> same-name swap (original kept as .orig-xvid) ->
receipt line. Originals are NEVER deleted by this script; the user reviews
the Recoded collection first.

Reads the audit list (codec|path lines), skips files already done (receipt).
Stop time (NZDT HH:MM) halts cleanly between files.
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HOST = "root@192.168.214.132"
REMOTE_BASE = "/mnt/zmedia/videos/"
AUDIT = Path("/root/.hermes/profiles/media-maintainer/state/xvid-full.txt")
RECEIPT = Path("/root/.hermes/profiles/media-maintainer/state/xvid-batch-receipt.jsonl")
STOP_HHMM = (5, 0)  # stop starting new files after 05:00 NZDT (next morning)


def shq(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def ssh(cmd: str, timeout: int = 1700) -> subprocess.CompletedProcess:
    # ffmpeg runs on the pool host; audit paths are relative, so all remote
    # file arguments below already carry the REMOTE_BASE prefix.
    return subprocess.run(
        ["ssh", "-o", "ConnectTimeout=12", HOST, cmd],
        capture_output=True, text=True, timeout=timeout,
    )


def past_stop() -> bool:
    now = datetime.now().astimezone()
    # Overnight batch: run from 22:00 until 05:00 next morning.
    # Current time is "past stop" only if we're in the 00:00-05:00 window.
    if now.hour >= 22:
        return False
    return (now.hour, now.minute) >= STOP_HHMM


def main() -> int:
    done = set()
    if RECEIPT.exists():
        for line in RECEIPT.read_text().splitlines():
            try:
                r = json.loads(line)
                if r.get("status") in ("SWAPPED", "SKIP-DONE"):
                    done.add(r.get("path"))
            except ValueError:
                pass
    todo = []
    for line in AUDIT.read_text().splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        _, path = line.split("|", 1)
        if path not in done:
            todo.append(path)
    print(f"todo: {len(todo)}", flush=True)
    for path in todo:
        if past_stop():
            print("STOP-TIME reached, halting between files", flush=True)
            break
        rpath = REMOTE_BASE + path
        p = Path(path)
        tmp = str(p.parent / (p.stem + ".x264-tmp.mkv"))
        bak = str(p.parent / (p.stem + ".orig-xvid" + p.suffix))
        rtmp = REMOTE_BASE + tmp
        rbak = REMOTE_BASE + bak
        rec: dict = {"path": path}
        # encode
        r = ssh(f"ffmpeg -y -v error -i {shq(rpath)} -map 0 "
                f"-c:v libx264 -preset veryfast -crf 20 "
                f"-c:a copy -c:s copy {shq(rtmp)}")
        if r.returncode != 0:
            rec.update(status="ENCODE-FAIL", err=r.stderr[-200:])
            print(f"FAIL-ENC {path[-60:]}", flush=True)
            ssh(f"rm -f {shq(rtmp)}")
            with RECEIPT.open("a") as f:
                f.write(json.dumps(rec) + "\n")
            continue
        # gate: duration + decode
        g = ssh(f"python3 -c \""
                f"import json,subprocess;"
                f"ds=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',{shq(rpath)}],capture_output=True,text=True).stdout.strip();"
                f"dn=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',{shq(rtmp)}],capture_output=True,text=True).stdout.strip();"
                f"print(ds+'|'+dn)\"")
        try:
            ds, dn = g.stdout.strip().split("|")
            ok_dur = abs(float(ds) - float(dn)) < max(5.0, float(ds) * 0.01)
        except ValueError:
            ok_dur = False
        d = ssh(f"ffmpeg -v error -xerror -i {shq(rtmp)} -f null -")
        if not ok_dur or d.returncode != 0:
            rec.update(status="GATE-FAIL", dur=f"{g.stdout.strip()}",
                       dec=d.returncode)
            print(f"FAIL-GATE {path[-60:]}", flush=True)
            ssh(f"rm -f {shq(rtmp)}")
            with RECEIPT.open("a") as f:
                f.write(json.dumps(rec) + "\n")
            continue
        # swap
        s = ssh(f"mv {shq(rpath)} {shq(rbak)} && mv {shq(rtmp)} {shq(rpath)} && echo OK")
        if "OK" not in s.stdout:
            rec.update(status="SWAP-FAIL", err=s.stderr[-200:])
            print(f"FAIL-SWAP {path[-60:]}", flush=True)
        else:
            rec.update(status="SWAPPED", backup=bak)
            print(f"SWAPPED {path[-60:]}", flush=True)
        with RECEIPT.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())