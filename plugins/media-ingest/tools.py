"""Tool handlers for the media-ingest plugin.

Each handler shells out to the corresponding `cli` stage, passing secrets
through the environment. That keeps ONE implementation: the tests that guard
the CLIs guard the agent path too.

Schemas declare the destructive surface honestly: media_apply takes an explicit
list of plan ids and nothing else.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict, List

try:  # the documented credential path on modern cores
    from agent.secret_scope import get_secret
except Exception:  # pragma: no cover - older core
    def get_secret(name: str, default: str = '') -> str:
        return os.environ.get(name, default)

MEDIA_OPS = '/root/.hermes/profiles/media-maintainer/media-ops'
TOOLSET = 'media'


def _error(msg: str) -> str:
    return json.dumps({'error': str(msg)}, ensure_ascii=False)


def _env() -> Dict[str, str]:
    env = dict(os.environ)
    for key in ('JELLYFIN_URL', 'JELLYFIN_API_KEY', 'JELLYFIN_USER_ID',
                'TMDB_API_KEY', 'MEDIA_IMAGE', 'QB_URL', 'QB_USER', 'QB_PASS',
                'PROWLARR_URL', 'PROWLARR_API_KEY', 'SABNZBD_URL', 'SABNZBD_API_KEY'):
        val = get_secret(key, '') or ''
        if val:
            env[key] = val
    return env


def _run(module: str, args: List[str], timeout: int = 1800) -> Dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, '-m', f'cli.{module}', *args],
        cwd=MEDIA_OPS, env=_env(), capture_output=True, text=True, timeout=timeout)
    return {
        'module': module,
        'returncode': proc.returncode,
        'stdout': proc.stdout[-4000:],
        'stderr': proc.stderr[-2000:],
    }


def _artifact(name: str) -> Any:
    path = os.path.join('/root/.hermes/profiles/media-maintainer/state', name)
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------------ scan
SCAN_SCHEMA = {
    'name': 'media_scan',
    'description': (
        'Read-only. List completed video downloads that are not yet in the ingest '
        'ledger, probe each one, and write the scan artefact. Returns the candidate '
        'titles with their real resolution, codec and container.'),
    'parameters': {'type': 'object', 'properties': {}, 'required': []},
}


def _handle_scan(args: dict, **kw) -> str:
    res = _run('scan', [])
    if res['returncode'] != 0:
        return _error(f"scan failed: {res['stderr'] or res['stdout']}")
    scan = _artifact('scan.json') or {}
    entries = [{'title': e.get('name'), 'hash': (e.get('hash') or '')[:12],
                'res': (f"{e['probe'].get('w')}x{e['probe'].get('h')} {e['probe'].get('codec')}"
                        if (e.get('probe') or {}).get('h') else 'PROBE-FAIL'),
                'container': e.get('head')}
               for e in (scan.get('entries') or [])]
    return json.dumps({'candidates': entries, 'count': len(entries)}, ensure_ascii=False)


# ------------------------------------------------------------------ plan
PLAN_SCHEMA = {
    'name': 'media_plan',
    'description': (
        'Read-only. Match every scanned candidate against the live library and build '
        'the decision table. Returns the REPLACE candidates (numbered ids) plus a '
        'summary of rows routed to a human. Nothing is changed.'),
    'parameters': {
        'type': 'object',
        'properties': {
            'no_tmdb': {'type': 'boolean',
                        'description': 'Skip TMDB annotation for NEW rows.'},
        },
        'required': [],
    },
}


def _handle_plan(args: dict, **kw) -> str:
    cmd = ['--no-tmdb'] if args.get('no_tmdb') else []
    res = _run('plan', cmd)
    if res['returncode'] != 0:
        return _error(f"plan failed: {res['stderr'] or res['stdout']}")
    plan = _artifact('plan.json') or []
    check = _artifact('needs-your-check.json') or []
    counts: Dict[str, int] = {}
    for r in check:
        k = str(r.get('recommend'))
        counts[k] = counts.get(k, 0) + 1
    return json.dumps({
        'replaces': [{'id': r['id'], 'title': r.get('title'),
                      'from': r.get('old_res'), 'to': r.get('new_res'),
                      'library': r.get('library')} for r in plan],
        'needs_human': counts,
        'stdout': res['stdout'][-1500:],
    }, ensure_ascii=False)


# ----------------------------------------------------------------- apply
APPLY_SCHEMA = {
    'name': 'media_apply',
    'description': (
        'DESTRUCTIVE. Execute the listed plan ids only (each must be a REPLACE row). '
        'Performs the verified two-phase swap (copy, probe, decode, tag and duration '
        'gates, then retire the old file), journals each completed item immediately, '
        'and refreshes the affected libraries. There is no "all" option by design.'),
    'parameters': {
        'type': 'object',
        'properties': {
            'ids': {'type': 'array', 'items': {'type': 'integer'},
                    'description': 'Plan ids to apply, taken from media_plan.'},
            'delete_source': {
                'type': 'boolean',
                'description': ('After a verified swap, delete the torrent and its '
                                'files (full 40-char hashes, re-listed afterwards).')},
        },
        'required': ['ids'],
    },
}


def _handle_apply(args: dict, **kw) -> str:
    ids = args.get('ids') or []
    if not isinstance(ids, list) or not ids:
        return _error('media_apply requires a non-empty list of plan ids')
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        return _error('ids must be integers')
    cmd = ['--ids', ','.join(str(i) for i in ids)]
    if args.get('delete_source'):
        cmd.append('--delete-source')
    res = _run('apply', cmd, timeout=7200)
    if res['returncode'] not in (0, 1):
        return _error(f"apply failed: {res['stderr'] or res['stdout']}")
    return json.dumps({'ok': res['returncode'] == 0,
                       'stdout': res['stdout'][-3000:]}, ensure_ascii=False)


# ---------------------------------------------------------------- verify
VERIFY_SCHEMA = {
    'name': 'media_verify',
    'description': (
        'Read-only. Report the 4-field exit gate (Tmdb, Imdb, primary image, '
        'overview >50 chars) for the given item ids, or for the plan\'s items when '
        'no ids are supplied.'),
    'parameters': {
        'type': 'object',
        'properties': {
            'item_ids': {'type': 'array', 'items': {'type': 'string'},
                         'description': 'Jellyfin item ids to check.'},
        },
        'required': [],
    },
}


def _handle_verify(args: dict, **kw) -> str:
    ids = args.get('item_ids') or []
    cmd = ['--json']
    if ids:
        cmd += ['--ids', ','.join(str(i) for i in ids)]
    res = _run('verify', cmd)
    if res['returncode'] != 0:
        return _error(f"verify failed: {res['stderr'] or res['stdout']}")
    return res['stdout'].strip() or json.dumps([])


# ------------------------------------------------------------------ source
SOURCE_PLAN_SCHEMA = {
    'name': 'media_source_plan',
    'description': (
        'Read-only. Sweep Prowlarr broadly (torrent + usenet), match wanted '
        'albums/works in code, and return a NUMBERED shortlist. Complete sets '
        'rank above incomplete ones and advertised seeds never promote an '
        'incomplete or SD posting. Nothing is downloaded.'),
    'parameters': {
        'type': 'object',
        'properties': {
            'targets': {
                'type': 'array',
                'description': 'Wanted works as {"artist","album"} objects.',
                'items': {'type': 'object'},
            },
            'protocol': {'type': 'string', 'enum': ['torrent', 'usenet'],
                         'description': 'Restrict to one protocol.'},
        },
        'required': ['targets'],
    },
}

SOURCE_QUEUE_SCHEMA = {
    'name': 'media_source_queue',
    'description': (
        'DESTRUCTIVE (spends bandwidth / Usenet block data). Queue explicit '
        'media_source_plan ids only — there is no "all" switch. Incomplete '
        '1ofN postings are refused unless allow_incomplete is set.'),
    'parameters': {
        'type': 'object',
        'properties': {
            'ids': {'type': 'array', 'items': {'type': 'integer'},
                    'description': 'Plan ids from media_source_plan.'},
            'allow_incomplete': {'type': 'boolean',
                                 'description': 'Permit 1ofN postings.'},
            'category': {'type': 'string', 'description': 'Downloader category.'},
        },
        'required': ['ids'],
    },
}


def _handle_source_plan(args: dict, **kw) -> str:
    targets = args.get('targets') or []
    if not targets:
        return _error('targets is required: [{"artist":..., "album":...}]')
    tpath = os.path.join(os.environ.get('MEDIA_STATE_DIR', '/tmp'), 'source-targets.json')
    with open(tpath, 'w') as fh:
        json.dump(targets, fh)
    cmd = ['plan', '--targets', tpath]
    if args.get('protocol'):
        cmd += ['--protocol', args['protocol']]
    res = _run('source', cmd)
    if res['returncode'] not in (0, 1):
        return _error(f"source plan failed: {res['stderr'] or res['stdout']}")
    plan = _artifact('source-plan.json') or {}
    rows = []
    for row in plan.get('rows') or []:
        for p in (row.get('picks') or [])[:1]:
            rows.append({'id': row.get('id'), 'want': f"{row.get('artist')} — {row.get('album')}",
                         'title': p.get('title'), 'protocol': p.get('protocol'),
                         'gb': p.get('gb'), 'seeds': p.get('seeds'),
                         'complete': p.get('complete'), 'grabs': p.get('grabs')})
    return json.dumps({'picks': rows, 'found': len(rows), 'wanted': len(targets),
                       'stdout': res['stdout'][-600:]}, ensure_ascii=False)


def _handle_source_queue(args: dict, **kw) -> str:
    ids = args.get('ids') or []
    if not ids:
        return _error('ids is required — queue explicit plan ids, never "all"')
    cmd = ['queue', '--ids', ','.join(str(i) for i in ids)]
    if args.get('allow_incomplete'):
        cmd.append('--allow-incomplete')
    if args.get('category'):
        cmd += ['--category', args['category']]
    res = _run('source', cmd)
    return json.dumps({'returncode': res['returncode'],
                       'stdout': res['stdout'][-1200:]}, ensure_ascii=False)

def _check_available() -> bool:
    return bool(get_secret('JELLYFIN_API_KEY', '') or os.environ.get('JELLYFIN_API_KEY'))


TOOLS = (
    (SCAN_SCHEMA, _handle_scan),
    (PLAN_SCHEMA, _handle_plan),
    (APPLY_SCHEMA, _handle_apply),
    (VERIFY_SCHEMA, _handle_verify),
    (SOURCE_PLAN_SCHEMA, _handle_source_plan),
    (SOURCE_QUEUE_SCHEMA, _handle_source_queue),
)


def register_tools(ctx) -> None:
    for schema, handler in TOOLS:
        ctx.register_tool(
            name=schema['name'], toolset=TOOLSET, schema=schema,
            handler=handler, check_fn=_check_available, emoji='🎬')
