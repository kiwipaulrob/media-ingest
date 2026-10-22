"""Candidate-bio screening: sense lists, disambiguation pages, crew provenance.

Every string here is a real provider response that reached the plan as `ready`
and would have been written onto a person row. They are the regression.
"""
from media_lib.providers import chain

# Verbatim from Last.fm for "Chris White" — 530 chars, passed the old length bar.
SENSE_LIST = (
    "There is more than one artist with the name Chris White: 1) an English jazz/rock "
    "saxophonist who toured with Dire Straits from 1985-1995 2) the former bass guitarist "
    "and songwriter for the 60s British Invasion band The Zombies 3) an Austrian musician "
    "who makes \"commercial music\" with German texts 4) a British film composer based in "
    "London who writes, arranges, and conducts musical scores for films"
)

# Verbatim from Wikipedia for "Bernard MacMahon" — the title gate cannot reject a
# disambiguation page because its title IS the exact name.
DISAMBIGUATION = (
    "Bernard MacMahon or McMahon may refer to: Bernard MacMahon (bishop) (1680-1747), "
    "Irish Roman Catholic prelate; Archbishop of Armagh Bernard MacMahon (bible "
    "translator) (c. 1736-1816), Irish Catholic priest, who translated the Douay-Rheims "
    "New Testament and the complete Bible Bernard McMahon (died 1816), Irish-American "
    "horticulturist, most notably the plantsman"
)

# A real biography that must never be screened out.
REAL_BIO = (
    "Robert Anthony Plant CBE (born 20 August 1948, West Bromwich, England) is an English "
    "singer, songwriter, and musician, best known as the lead singer and lyricist of the "
    "rock band Led Zeppelin."
)

# The documented blind spot: a well-formed life about the WRONG Peter Grant.
WRONG_PERSON_REAL_BIO = (
    "Peter Grant is a British singer who achieved success in early 2006 with his debut "
    "album New Vintage. He began singing at around the age of six or seven and played at "
    "working men's clubs from the age of 13."
)


def test_lastfm_multi_artist_blurb_is_a_sense_list():
    assert chain.bio_kind(SENSE_LIST) == "sense-list"
    assert chain.bio_implausible(SENSE_LIST) == "junk-multiartist-or-disambiguation"


def test_disambiguation_page_is_rejected_on_content():
    assert chain.bio_kind(DISAMBIGUATION) == "sense-list"
    assert chain.bio_implausible(DISAMBIGUATION)


def test_numbered_senses_without_the_phrase_are_still_caught():
    # No "more than one artist" wording — only the enumeration gives it away.
    text = ("1) an English saxophonist who toured with Dire Straits 2) the former bass "
            "guitarist and songwriter for The Zombies 3) an Austrian musician")
    assert chain.bio_kind(text) == "sense-list"


def test_a_real_biography_passes():
    assert chain.bio_kind(REAL_BIO) == "biography"
    assert chain.bio_implausible(REAL_BIO) is None


def test_crew_credit_must_not_take_prose_from_a_music_source():
    # The skill's "never run crew gaps through musician-only sources", now in code.
    assert (chain.collision_reason(REAL_BIO, credit_type="Director", source="lastfm")
            == "crew-credit-from-music-source:lastfm")
    # ...but the same prose is fine from a film-literate source.
    assert chain.collision_reason(REAL_BIO, credit_type="Director", source="wikipedia") is None
    # ...and a music source is legitimate when the person IS the performer.
    assert chain.collision_reason(REAL_BIO, credit_type="Actor", source="lastfm") is None


def test_collision_reason_reports_junk_before_provenance():
    assert chain.collision_reason(SENSE_LIST, credit_type="Actor", source="lastfm")


def test_wrong_person_biography_is_a_known_blind_spot():
    """Guard the honest limit so nobody assumes this is solved.

    This is a coherent biography of a different Peter Grant (Led Zeppelin's manager
    died in 1995). No text heuristic can tell it from the right one, so it must
    still reach a human via the identity gate. If this test ever starts failing
    because the text IS caught, delete it and celebrate — but do not widen a regex
    to make it pass by accident.
    """
    assert chain.bio_kind(WRONG_PERSON_REAL_BIO) == "biography"
    assert chain.collision_reason(WRONG_PERSON_REAL_BIO, credit_type="Actor",
                                 source="lastfm") is None


def test_nineteenth_century_life_still_rejected():
    assert chain.bio_implausible(
        "Reverend Arthur Meagher Cave (1883-1948) was a clergyman and naval officer."
    ) == "pre-1900-birth-in-text"


# --- profession conflict: a crew credit carrying an unrelated life ------------
# Verbatim from TMDB for a Director credit on a music documentary, 1100 chars,
# which the plan scored `ready` before this rule existed.
WRESTLER_ON_A_DIRECTOR_CREDIT = (
    "George Scott was a Canadian professional wrestler, booker and promoter. From "
    "the 1950s until the 1970s, he and his younger brother Sandy competed as The "
    "Flying Scotts in the National Wrestling Alliance."
)


def test_wrestler_on_a_director_credit_is_flagged():
    assert (chain.collision_reason(WRESTLER_ON_A_DIRECTOR_CREDIT, credit_type="Director",
                                   source="tmdb")
            == "profession-conflict-for-crew:wrestler")


def test_the_same_bio_on_an_actor_credit_is_fine():
    # A wrestler who appears in a documentary IS the subject, not crew.
    assert chain.collision_reason(WRESTLER_ON_A_DIRECTOR_CREDIT, credit_type="Actor",
                                 source="tmdb") is None


def test_a_filmmaker_who_mentions_a_boxer_is_not_flagged():
    # Only the opening counts — a director's bio may name someone they filmed.
    text = ("Jane Doe is a British documentary director. Her 2019 film followed a "
            "retired boxer through his comeback, and she also worked with a "
            "former footballer on a series about grassroots sport.")
    assert chain.profession_conflict(text, "Director") is None


def test_musician_on_a_crew_credit_is_not_a_profession_conflict():
    # Musicians direct music documentaries constantly; this must stay allowed.
    text = ("Colin Blunstone is an English singer and songwriter, best known as the "
            "lead singer of the rock band The Zombies, and he has directed two films.")
    assert chain.profession_conflict(text, "Director") is None


def test_general_as_an_adjective_is_not_a_military_general():
    # Both were flagged before the phrase-end guard: 'general' is a common adjective.
    assert chain.profession_conflict(
        "John Smith was a general manager for the band from 1998.", "Director") is None
    assert chain.profession_conflict(
        "She was a general practitioner before turning to film.", "Director") is None


def test_a_real_military_general_is_still_caught():
    assert (chain.profession_conflict("He was a general in the Army before film.", "Director")
            == "profession-conflict-for-crew:general")


def test_occupation_followed_by_a_conjunction_still_counts():
    # 'was a wrestler, booker and promoter' — the George Scott shape.
    assert chain.profession_conflict(
        "He was a wrestler, booker and promoter in the 1950s.", "Director")
