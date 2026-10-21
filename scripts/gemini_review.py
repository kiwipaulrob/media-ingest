#!/usr/bin/env python3
"""Ask Gemini to review the media-ingest repo via OpenRouter.

Why this route: the machine's Google API keys return 402 (prepayment credits
depleted), so neither Gemini CLI nor the Google API can run. OpenRouter is
already configured with google/gemini-3.8-flash and a working key, so the same
model reviews the code without touching the depleted project.

Usage:  python3 scripts/gemini_review.py [repo_path]
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

INCLUDE_DIRS = ('media_lib', 'cli')
INCLUDE_FILES = ('README.md', 'docs/INGEST.md')
MAX_FILE_BYTES = 60_000
MODEL = os.environ.get('REVIEW_MODEL', 'google/gemini-3.8-flash')

PROMPT = """You are a senior Python reviewer. The repository below is a media-ingest
pipeline for a Jellyfin homelab: read-only stages (scan, plan, verify) and
mutating stages (apply = replace an existing file, ingest_new = place a new file),
plus a metadata provider chain.

Review it critically. Do NOT summarise the README back to me. Structure your answer:

1. THREE correctness risks, each naming a file and function, and a concrete input
   or condition that triggers it. Prefer risks that tests would not catch.
2. TWO design weaknesses, with the smallest change that would fix each.
3. The single most valuable missing test, and what it should assert.
4. One thing the code does unusually well.

Be specific and blunt. If something is fine, say so briefly and move on.
"""


def collect(repo: Path):
    parts = []
    for name in INCLUDE_FILES:
        p = repo / name
        if p.is_file():
            parts.append((str(p.relative_to(repo)), p.read_text(errors='replace')))
    for d in INCLUDE_DIRS:
        for p in sorted((repo / d).rglob('*.py')):
            if '__pycache__' in p.parts:
                continue
            txt = p.read_text(errors='replace')
            if len(txt) > MAX_FILE_BYTES:
                txt = txt[:MAX_FILE_BYTES] + '\n... truncated ...\n'
            parts.append((str(p.relative_to(repo)), txt))
    return parts


def main() -> int:
    repo = Path(sys.argv[1] if len(sys.argv) > 1 else '.').resolve()
    key = os.environ.get('OPENROUTER_API_KEY', '')
    if not key:
        print('OPENROUTER_API_KEY not set', file=sys.stderr)
        return 2

    files = collect(repo)
    total = sum(len(t) for _, t in files)
    print(f'files: {len(files)} | chars: {total:,} | model: {MODEL}', file=sys.stderr)

    body = '\n\n'.join(f'===== FILE: {name} =====\n{text}' for name, text in files)
    payload = {
        'model': MODEL,
        'messages': [
            {'role': 'system', 'content': PROMPT},
            {'role': 'user', 'content': f'Repository files:\n\n{body}'},
        ],
        'temperature': 0.2,
        'max_tokens': 4000,
    }
    req = urllib.request.Request(
        'https://openrouter.ai/api/v1/chat/completions',
        data=json.dumps(payload).encode(),
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
                 'HTTP-Referer': 'https://github.com/kiwipaulrob/media-ingest',
                 'X-Title': 'media-ingest review'},
    )
    with urllib.request.urlopen(req, timeout=420) as fh:
        data = json.loads(fh.read() or b'{}')
    if 'error' in data:
        print('API ERROR:', json.dumps(data['error'])[:400], file=sys.stderr)
        return 1
    choice = (data.get('choices') or [{}])[0]
    print(choice.get('message', {}).get('content') or '(empty response)')
    usage = data.get('usage') or {}
    print(f"\n--- usage: {usage.get('prompt_tokens')} in / {usage.get('completion_tokens')} out ---",
          file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
