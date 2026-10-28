"""Matcher corpus — the load-bearing suite of the whole refactor.

Every case here is either a real incident (a wrong file written into the
library) or a real false negative measured against the production matcher on
2026-10-20. If this file goes red, ingest must not run unattended.
"""
import pytest

from media_lib import matcher, tmdb


def lib(*names, lib_name='Films'):
    """Synthetic library dump with one row per name (isolation, per plan)."""
    rows = [{'Id': f'id-{i}', 'Name': n, 'Path': f'/data/movies/{n}/f.mkv', 'nsrc': 1}
            for i, n in enumerate(names)]
    return {'libs': {lib_name: rows}}


# --------------------------------------------------------------- must REFUSE
REFUSE = [
    # The incident: a 1080p 'Good Hair' download was written over 'Good (2008)'.
    ('Good.Hair', 'Good'),
    ('Good.Hair.2009.1080p.BluRay.x264', 'Good'),
    # The incident: sharing only 'the story of the' matched an unrelated film.
    ('Smash.and.Grab.The.Story.of.the.Pink.Panthers.2011',
     'The Story Of The Guitar'),
    # Sequels must not absorb each other.
    ('The.Matrix.Reloaded.2003.1080p', 'The Matrix'),
    ('Batman.Begins.2005.1080p', 'The Dark Knight'),
]

# --------------------------------------------------------------- must MATCH
MATCH = [
    ('Good.Hair', 'Good Hair'),
    ('Good.Hair.2009.1080p.BluRay.x264', 'Good Hair'),
    # FIX 2: release noise must not poison coverage (measured broken before).
    ('Anvil.The.Story.Of.Anvil.2008.1080p.BluRay.x264-GRP',
     'Anvil! The Story of Anvil'),
    # FIX 1: run-together name, zero token overlap (measured broken before).
    ('TheWarOnDemocracy', 'The War on Democracy'),
    ('The.Dust.Bowl', 'The Dust Bowl'),
    ('The.Matrix.1999.1080p.BluRay.x264-GRP', 'The Matrix'),
    ('The.Matrix', 'The Matrix'),
]

# --------------------------------------------------------------- need REVIEW
REVIEW = [
    ('Up', 'Up (2009)'),                 # 2-char title: too weak either way
    ('Up.2009.1080p.BluRay', 'Up'),      # release matches a 2-char target
    # Corrected from REFUSE during implementation: the release carries ONE
    # distinctive token and adds no title word the target lacks, so it could be
    # a truncated form of the target. The skill's rule — short token sets are
    # unmatchable -> REVIEW — is the safer call than REJECT (REJECT means 'NEW'
    # and would create a duplicate row if the library copy is simply renamed).
    ('Batman', 'Batman Begins'),
]


@pytest.mark.parametrize('release,target', REFUSE)
def test_known_false_pairs_are_refused(release, target):
    v = matcher.match(release, lib(target))
    assert v['decision'] == matcher.REJECT, v


@pytest.mark.parametrize('release,target', MATCH)
def test_known_true_pairs_are_accepted(release, target):
    v = matcher.match(release, lib(target))
    assert v['decision'] == matcher.MATCH, v


@pytest.mark.parametrize('release,target', REVIEW)
def test_ambiguous_pairs_go_to_a_human(release, target):
    v = matcher.match(release, lib(target))
    assert v['decision'] == matcher.REVIEW, v


def test_refusal_never_depends_on_the_absence_of_a_better_match():
    """The incident's real shape: the wrong item was present and scored best.

    A library holding BOTH 'Good' and 'Good Hair' must route 'Good.Hair' to the
    correct row, and must still refuse when only 'Good' is present.
    """
    both = matcher.match('Good.Hair', lib('Good', 'Good Hair'))
    assert both['decision'] == matcher.MATCH
    assert both['item']['Name'] == 'Good Hair'

    only_wrong = matcher.match('Good.Hair', lib('Good'))
    assert only_wrong['decision'] == matcher.REJECT


def test_containment_alone_is_never_evidence():
    """'good' inside 'goodhair' is 0.5 of the longer string — not a match."""
    v = matcher.match('Good', lib('Good Hair'))
    assert v['decision'] in (matcher.REJECT, matcher.REVIEW), v


# ------------------------------------------------------------------ units
def test_clean_release_keeps_hyphenated_titles():
    assert matcher.clean_release('Spider-Man.2002.1080p.BluRay') == 'Spider-Man 2002'
    assert 'Spider-Man' in matcher.clean_release('Spider-Man')


def test_clean_release_does_not_use_splitext_on_dotted_names():
    # os.path.splitext('Good.Hair') == ('Good', '.Hair') — the bug.
    assert matcher.clean_release('Good.Hair') == 'Good Hair'


def test_clean_release_strips_brand_abbreviations():
    # 'WAC' (Warner Archive Collection) as a bare token poisoned the TMDB query
    # to 'The Set-Up WAC' = no-match, so a new film never got a gate candidate.
    assert matcher.clean_release(
        'The.Set-Up.1949.WAC.1080p.BluRay.x265.HEVC.AAC-SARTRE'
    ) == 'The Set-Up 1949'
    assert tmdb.title_year('The.Set-Up.1949.WAC.1080p.BluRay.x265.HEVC.AAC-SARTRE') == (
        'The Set-Up',
        '1949',
    )


def test_strip_extension_only_removes_real_video_extensions():
    assert matcher.strip_extension('Film.2020.mkv') == 'Film.2020'
    assert matcher.strip_extension('Good.Hair') == 'Good.Hair'
    assert matcher.strip_extension('Anvil...x264-GRP') == 'Anvil...x264-GRP'


def test_squash_is_alnum_lowercase():
    assert matcher.squash('The War on Democracy!') == 'thewarondemocracy'


def test_empty_release_is_review_not_match():
    assert matcher.match('', lib('Anything'))['decision'] == matcher.REVIEW
    assert matcher.match('   ', lib('Anything'))['decision'] == matcher.REVIEW


def test_empty_library_is_reject():
    assert matcher.match('Some Film 2020', lib())['decision'] == matcher.REJECT


def test_flat_list_dump_shape_is_accepted():
    v = matcher.match('The.Dust.Bowl', [{'Name': 'The Dust Bowl'}])
    assert v['decision'] == matcher.MATCH
