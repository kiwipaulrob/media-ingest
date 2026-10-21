#!/usr/bin/env python3
"""Audit (and optionally repair) Matroska seek indexes for Jellyfin clients.

Why this exists
---------------
ExoPlayer / Media3 (the player inside Jellyfin for Android and Android TV) builds a
player *SeekMap* ONLY from a Cues entry that appears as a Seek entry in the file's
LEADING SeekHead. It records nothing else from that SeekHead:

    case ID_SEEK:
      if (seekEntryId == ID_CUES) { cuesContentPosition = seekEntryPosition; }

and at the first Cluster:

    if (seekForCuesEnabled && cuesContentPosition != C.INDEX_UNSET) seekForCues = true;
    else extractorOutput.seekMap(new SeekMap.Unseekable(durationUs));  // "Allow playback, but disable seeking."

A chained SeekHead (leading SeekHead -> trailing SeekHead -> Cues, which ffmpeg/VLC/mpv
follow happily) is therefore invisible to these clients: the file PLAYS but the seek
bar / fast-forward is dead. Verified on Later with Jools Holland S68E06, 2026-10-21.

Usage (run where the media lives; CT105 has python3 and /mnt/zmedia):
    python3 mkv-seekindex-audit.py /mnt/zmedia/videos/<lib> [...]
    python3 mkv-seekindex-audit.py --fix /mnt/zmedia/videos/<lib>/Season 68

--fix is a 17-byte in-place rewrite of one Seek entry (same size, no data movement,
file size unchanged, so Jellyfin's DB row and mtime-driven rescans are untouched).
It backs up the first 8 KiB to /root/<basename>.head8k.bak first.

EBML gotcha this script encodes correctly: a size vint's length is the number of
LEADING ONE BITS of its first byte (0xC2 = 2-byte vint). Treating the first byte as a
single marker bit makes every element after the first parse as garbage.
"""
import argparse
import os
import sys

EBML_HDR = 0x1A45DFA3
SEGMENT = 0x18538067
SEEK_HEAD = 0x114D9B74
SEEK = 0x4DBB
SEEK_ID = 0x53AB
SEEK_POS = 0x53AC
CUES = 0x1C53BB6B
TAGS = 0x1254C367
CHAPTERS = 0x1043A770
INFO = 0x1549A966
TRACKS = 0x1654AE6B

NAMES = {SEEK_HEAD: 'SeekHead', CUES: 'Cues', TAGS: 'Tags', CHAPTERS: 'Chapters',
         INFO: 'Info', TRACKS: 'Tracks', SEGMENT: 'Segment', EBML_HDR: 'EBML'}


def read_id(buf, i):
    b0 = buf[i]
    n = 1
    mask = 0x80
    while n <= 4 and not (b0 & mask):
        mask >>= 1
        n += 1
    if n > 4:
        raise ValueError('bad element id at %d' % i)
    return int.from_bytes(buf[i:i + n], 'big'), i + n, n


def read_size(buf, i):
    b0 = buf[i]
    if b0 == 0:
        raise ValueError('zero size byte at %d' % i)
    n = 1
    mask = 0x80
    while n <= 8 and not (b0 & mask):
        mask >>= 1
        n += 1
    if n > 8:
        raise ValueError('bad size vint at %d' % i)
    value = b0 & (mask - 1)
    for k in range(1, n):
        value = (value << 8) | buf[i + k]
    return value, i + n, n


def read_elem(buf, i):
    eid, j, _ = read_id(buf, i)
    size, ds, szlen = read_size(buf, j)
    return eid, size, ds, szlen


def parse_seek_head(buf, ds, size, seg_data_start, abs_base):
    """Return list of dicts: target id, relative pos, abs offset of the entry."""
    out = []
    content = buf[ds:ds + size]
    k = 0
    while k < len(content) - 3:
        start = k
        try:
            eid, csz, cds, szlen = read_elem(content, k)
        except ValueError:
            break
        if eid != SEEK:
            k = cds + csz
            continue
        ent = content[cds:cds + csz]
        try:
            sid, ssz, sds, _ = read_elem(ent, 0)
            posid, psz, pds, _ = read_elem(ent, sds + ssz)
        except ValueError:
            k = cds + csz
            continue
        if sid != SEEK_ID or posid != SEEK_POS:
            k = cds + csz
            continue
        out.append({
            'target': int.from_bytes(ent[sds:sds + ssz], 'big'),
            'rel_pos': int.from_bytes(ent[pds:pds + psz], 'big'),
            'entry_abs': abs_base + ds + start,
            'entry_len': cds + csz - start,
            'pos_len': psz,
        })
        k = cds + csz
    return out


def leading_seek_head(f, filesize):
    """Locate the segment's leading SeekHead among the first few children."""
    f.seek(0)
    head = f.read(8192)
    eid, size, ds, _ = read_elem(head, 0)
    if eid != EBML_HDR:
        raise ValueError('not an EBML/Matroska file')
    seg_off = ds + size
    eid2, seg_size, seg_ds, _ = read_elem(head, seg_off)
    if eid2 != SEGMENT:
        raise ValueError('no Segment element')
    i = seg_ds
    for _ in range(6):
        eid3, sz3, ds3, _ = read_elem(head, i)
        if eid3 == SEEK_HEAD:
            return seg_ds, head, i, sz3, ds3
        if sz3 < 0 or sz3 > filesize:
            break
        i = ds3 + sz3
    raise ValueError('no leading SeekHead in the first 8 KiB')


def find_cues(f, filesize, tail_bytes=4 * 1024 * 1024):
    """Find the Cues element by scanning the tail; validate it parses."""
    start = max(0, filesize - tail_bytes)
    f.seek(start)
    buf = f.read()
    cands = []
    p = 0
    while True:
        k = buf.find(b'\x1c\x53\xbb\x6b', p)
        if k < 0:
            break
        cands.append(start + k)
        p = k + 1
    for abs_off in cands:
        try:
            f.seek(abs_off)
            hdr = f.read(16)
            eid, size, ds, _ = read_elem(hdr, 0)
            if eid != CUES:
                continue
            if not (100 < size < filesize) or abs_off + ds + size > filesize:
                continue
            f.seek(abs_off + ds)
            body = f.read(min(size, 65536))
            # count CuePoint ids to prove it is really the index, not payload noise
            if body.count(b'\xbb') < 10:
                continue
            return abs_off, size, ds
        except ValueError:
            continue
    return None


def audit(path):
    filesize = os.path.getsize(path)
    with open(path, 'rb') as f:
        first = f.read(8)
        if first[:4] != b'\x1a\x45\xdf\xa3':
            return {'status': 'NOT-EBML', 'detail': first[:8].hex()}
        seg_ds, head, sh_off, sh_size, sh_ds = leading_seek_head(f, filesize)
        entries = parse_seek_head(head, sh_ds, sh_size, seg_ds, 0)
        targets = [e['target'] for e in entries]
        info = {'status': 'SEEKABLE' if CUES in targets else 'NO-CUES',
                'entries': [(NAMES.get(t, hex(t)), e['rel_pos']) for t, e in zip(targets, entries)],
                'seg_ds': seg_ds, 'filesize': filesize}
        if CUES not in targets:
            cues = find_cues(f, filesize)
            info['cues_found_at'] = cues[0] if cues else None
        return info


def patch(path, backup_dir='/root'):
    """Replace one non-Cues Seek entry with a Cues entry, same byte length."""
    filesize = os.path.getsize(path)
    with open(path, 'rb') as f:
        seg_ds, head, sh_off, sh_size, sh_ds = leading_seek_head(f, filesize)
        entries = parse_seek_head(head, sh_ds, sh_size, seg_ds, 0)
        if any(e['target'] == CUES for e in entries):
            return 'already-seekable'
        cues = find_cues(f, filesize)
        if not cues:
            return 'cues-not-found'
        cues_abs, cues_size, cues_ds = cues
        rel = cues_abs - seg_ds

        # prefer to drop a Tags pointer; never drop a Tracks pointer
        victim = None
        for prefer in (TAGS, SEEK_HEAD, CHAPTERS):
            for e in entries:
                if e['target'] == prefer:
                    victim = e
                    break
            if victim:
                break
        if victim is None:
            return 'no-safe-entry-to-replace'

        # build the replacement entry at exactly the same length as the victim
        def size_vint(v, n):
            if v >= (1 << (7 * n)):
                raise ValueError('size too big for %d bytes' % n)
            return (v | (1 << (7 * n))).to_bytes(n, 'big')

        def enc_seek(target, pos, pos_len, total_len):
            body = b'\x53\xab' + size_vint(4, 1) + target.to_bytes(4, 'big')
            body += b'\x53\xac' + size_vint(pos_len, 1) + pos.to_bytes(pos_len, 'big')
            for szlen in range(1, 9):
                try:
                    enc = b'\x4d\xbb' + size_vint(len(body), szlen) + body
                except ValueError:
                    continue
                if len(enc) == total_len:
                    return enc
            return None

        replacement = enc_seek(CUES, rel, victim['pos_len'], victim['entry_len'])
        if replacement is None:
            return 'cannot-encode-same-length'

        # backup + write
        bak = os.path.join(backup_dir, os.path.basename(path) + '.head8k.bak')
        if not os.path.exists(bak):
            with open(bak, 'wb') as b:
                f.seek(0)
                b.write(f.read(8192))
        with open(path, 'r+b') as w:
            w.seek(victim['entry_abs'])
            before = w.read(len(replacement))
            w.seek(victim['entry_abs'])
            w.write(replacement)
            w.flush()
            os.fsync(w.fileno())

    # verify (and roll back on any failure)
    try:
        ok = audit(path)['status'] == 'SEEKABLE'
    except Exception:
        ok = False
    if not ok:
        with open(path, 'r+b') as w:
            w.seek(victim['entry_abs'])
            w.write(before)
            w.flush()
            os.fsync(w.fileno())
        return 'verify-failed-rolled-back'
    return 'patched -> Cues rel %d (abs %d), verified SEEKABLE' % (rel, cues_abs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('roots', nargs='+')
    ap.add_argument('--fix', action='store_true', help='repair NO-CUES files in place')
    ap.add_argument('--quiet-ok', action='store_true', help='only print problems')
    args = ap.parse_args()

    counts = {}
    for root in args.roots:
        paths = []
        if os.path.isfile(root):
            paths = [root]
        else:
            for dirpath, _, names in os.walk(root):
                for n in names:
                    if os.path.splitext(n)[1].lower() in ('.mkv', '.mp4', '.m4v'):
                        paths.append(os.path.join(dirpath, n))
        for p in sorted(paths):
            try:
                res = audit(p)
            except Exception as e:
                res = {'status': 'ERROR', 'detail': str(e)}
            status = res['status']
            counts[status] = counts.get(status, 0) + 1
            if status == 'SEEKABLE' and args.quiet_ok:
                continue
            print('%s\t%s\t%s' % (status, p, res.get('entries') or res.get('detail') or
                                  ('cues at %s' % res.get('cues_found_at'))))
            if status == 'NO-CUES' and args.fix:
                print('   FIX: %s' % patch(p))
    print('\nSUMMARY: ' + ', '.join('%s=%d' % kv for kv in sorted(counts.items())), file=sys.stderr)


if __name__ == '__main__':
    main()
