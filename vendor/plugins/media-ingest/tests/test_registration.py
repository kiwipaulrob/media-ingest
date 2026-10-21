"""Registration and safety-surface tests for the media-ingest plugin.

The important assertions are negative ones: the destructive tool must refuse to
run without explicit ids, and no tool may expose a "do everything" switch.
"""
import json

import media_ingest_plugin as pkg
from media_ingest_plugin import tools


class FakeCtx:
    def __init__(self):
        self.tools = {}

    def register_tool(self, name, toolset=None, schema=None, handler=None,
                      check_fn=None, emoji=None):
        self.tools[name] = {'toolset': toolset, 'schema': schema,
                            'handler': handler, 'check_fn': check_fn}


def test_register_exposes_exactly_four_tools():
    ctx = FakeCtx()
    pkg.register(ctx)
    assert set(ctx.tools) == {'media_scan', 'media_plan', 'media_apply', 'media_verify'}
    for name, t in ctx.tools.items():
        assert t['toolset'] == 'media', name
        assert t['handler'] is not None, name
        assert t['schema']['name'] == name


def test_no_tool_offers_a_do_everything_switch():
    for schema, _handler in tools.TOOLS:
        props = (schema.get('parameters') or {}).get('properties') or {}
        for banned in ('all', 'everything', 'force', 'yes'):
            assert banned not in props, f'{schema["name"]} exposes {banned!r}'


def test_apply_refuses_empty_ids():
    assert 'error' in json.loads(tools._handle_apply({'ids': []}))


def test_apply_refuses_missing_ids_key():
    assert 'error' in json.loads(tools._handle_apply({}))


def test_apply_refuses_non_integer_ids():
    assert 'error' in json.loads(tools._handle_apply({'ids': ['all']}))


def test_apply_schema_requires_ids():
    assert (tools.APPLY_SCHEMA['parameters'] or {}).get('required') == ['ids']


def test_read_only_and_destructive_tools_are_labelled_honestly():
    for schema, _ in tools.TOOLS:
        desc = schema['description'].lower()
        if schema['name'] in ('media_scan', 'media_plan', 'media_verify'):
            assert 'read-only' in desc, schema['name']
        if schema['name'] == 'media_apply':
            assert 'destructive' in desc
