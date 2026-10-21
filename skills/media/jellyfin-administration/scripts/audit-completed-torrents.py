#!/usr/bin/env python3
"""Audit 100%-complete qBittorrent torrents against the Jellyfin library.

Answers one question: is this torrent's source data provably ALREADY in the
library, so the source is redundant and safe to delete?

Proof chain for a single-file torrent (all four required):
  1. content_path exists on disk
  2. a library file of byte-identical size exists
  3. `cmp -s` confirms byte-for-byte equality
  4. that library filename has a Jellyfin row

Multi-file (album) torrents are reported as per-file coverage instead.

READ-ONLY. Deletes nothing. Run it, read the table, then delete deliberately.

Usage (on the host that holds both shares, i.e. proxmox3):
    qb_api /api/v2/torrents/info            -> /tmp/qall.json
    jellyfin /Items?Recursive=true&IncludeItemTypes=Movie,Video&Fields=Path
                                            -> /tmp/jf_paths.json
    python3 audit-completed-torrents.py
"""
import json
import os
import subprocess

LIB = "/mnt/dguh/videos"
# qBittorrent reports content_path in ITS container namespace. On the host the
# same bytes are here. Without this translation EVERY torrent falsely reports
# SOURCE MISSING -- a full page of confident, wrong output.
DL_PREFIX = "/downloads/"
HOST_DL = "/mnt/dfujh/downloads/"
AUDIO = (".flac", ".mp3", ".m4a", ".wav", ".ogg", ".ape", ".wv", ".aiff")


def build_library_index():
    """size -> [paths], plus basename -> {sizes} for the album pass."""
    by_size, by_name = {}, {}
    audio = total = 0
    out = subprocess.run(
        ["find", LIB, "-type", "f", "-printf", "%s\t%p\n"],
        capture_output=True, text=True,
    )
    for line in out.stdout.splitlines():
        try:
            sz, p = line.split("\t", 1)
            sz = int(sz)
        except ValueError:
            continue
        total += 1
        if p.lower().endswith(AUDIO):
            audio += 1
        by_size.setdefault(sz, []).append(p)
        by_name.setdefault(os.path.basename(p), set()).add(sz)
    return by_size, by_name, total, audio


def main():
    torrents = json.load(open("/tmp/qall.json"))
    torrents = [t for t in torrents if t.get("progress", 0) >= 1.0]
    jf_paths = json.load(open("/tmp/jf_paths.json"))
    jf_names = {os.path.basename(p) for p in jf_paths if p}
    jf_lower = {n.lower() for n in jf_names}

    by_size, by_name, total, audio = build_library_index()
    print(f"library: {total} files ({audio} audio), {len(by_size)} distinct sizes")
    print(f"jellyfin: {len(jf_names)} basenames | complete torrents: {len(torrents)}\n")

    rows = []
    for t in torrents:
        cp = t.get("content_path") or ""
        if cp.startswith(DL_PREFIX):
            cp = HOST_DL + cp[len(DL_PREFIX):]
        e = {"name": t.get("name", ""), "hash": t["hash"],
             "size": t.get("size", 0), "seeds": t.get("num_seeds"), "path": cp}

        if not cp or not os.path.exists(cp):
            e["verdict"] = "SOURCE MISSING"
        elif os.path.isfile(cp):
            fsz = os.path.getsize(cp)
            cands = by_size.get(fsz, [])
            match = next((c for c in cands
                          if subprocess.run(["cmp", "-s", cp, c]).returncode == 0), None)
            if not cands:
                e["verdict"] = "NOT IN LIBRARY"
            elif not match:
                e["verdict"] = "SIZE MATCH / DIFFERS"
            else:
                b = os.path.basename(match)
                ingested = b in jf_names or b.lower() in jf_lower
                e["verdict"] = "INGESTED" if ingested else "COPIED, NO JF ROW"
                e["library_file"] = match
        else:
            files = []
            for r, _d, fs in os.walk(cp):
                for f in fs:
                    try:
                        files.append((f, os.path.getsize(os.path.join(r, f))))
                    except OSError:
                        pass
            hit = sum(1 for f, sz in files if sz in by_name.get(f, set()))
            e["verdict"] = "ALBUM IN LIBRARY" if files and hit == len(files) else \
                           ("ALBUM NOT IN LIBRARY" if hit == 0 else "ALBUM PARTIAL")
            e["coverage"] = f"{hit}/{len(files)}"
        rows.append(e)

    json.dump(rows, open("/tmp/audit_results.json", "w"), indent=1)
    order = {"INGESTED": 0, "COPIED, NO JF ROW": 1, "SIZE MATCH / DIFFERS": 2,
             "ALBUM PARTIAL": 3, "ALBUM IN LIBRARY": 4, "ALBUM NOT IN LIBRARY": 5,
             "NOT IN LIBRARY": 6, "SOURCE MISSING": 7}
    print(f"{'GB':>6}  {'VERDICT':<20} {'COV':>8}  TORRENT")
    print("-" * 100)
    for e in sorted(rows, key=lambda z: (order.get(z["verdict"], 9), -z["size"])):
        print(f'{e["size"]/1e9:6.2f}  {e["verdict"]:<20} {e.get("coverage",""):>8}  {e["name"][:50]}')
    print()
    for v in order:
        g = [e for e in rows if e["verdict"] == v]
        if g:
            print(f"{v}: {len(g)} torrents, {sum(x['size'] for x in g)/1e9:.2f} GB")


if __name__ == "__main__":
    main()
