"""Tests for the football claim extractor and symbolic verifier.

Marked `no_db`: this layer is pure logic over a JSON graph and must stay testable
without a database.

The tests that matter most are the ones asserting that **missing evidence is not
reported as refutation**. A claim about a player the records have never heard of is
UNVERIFIED, not CONTRADICTED; getting that wrong would turn an incomplete graph into a
machine for calling true statements false.
"""
from __future__ import annotations

import pytest

from app.football.verifier import (
    CONTRADICTED, GOAL, MATCH_TOTAL, NOT_CHECKABLE, PLAYER_TOTAL, SUPPORTED,
    UNVERIFIED, Claim, FootballGraph, check_rules, extract_claim, extract_claims,
    gate, split_sentences, verify)

pytestmark = pytest.mark.no_db


@pytest.fixture
def graph() -> FootballGraph:
    return FootballGraph({
        "goals": [
            {"goal_id": "G-1", "match_id": "M-1", "tournament_id": "T-1",
             "player_id": "P-1", "player_name": "André Maschinot",
             "player_team": "France", "credited_team": "France", "minute": 43,
             "stoppage": 0, "period": "first half", "own_goal": False,
             "penalty": False},
            {"goal_id": "G-2", "match_id": "M-1", "tournament_id": "T-1",
             "player_id": "P-1", "player_name": "André Maschinot",
             "player_team": "France", "credited_team": "France", "minute": 87,
             "stoppage": 0, "period": "second half", "own_goal": False,
             "penalty": False},
            {"goal_id": "G-3", "match_id": "M-2", "tournament_id": "T-2",
             "player_id": "P-2", "player_name": "Ivan Bek", "player_team": "Yugoslavia",
             "credited_team": "Yugoslavia", "minute": 30, "stoppage": 0,
             "period": "first half", "own_goal": False, "penalty": False},
        ],
        "matches": [
            {"match_id": "M-1", "name": "France vs Mexico", "date": "1930-07-13",
             "stage": "group stage", "group": "Group 1", "tournament_id": "T-1",
             "teams": ["France", "Mexico"]},
            {"match_id": "M-2", "name": "Yugoslavia vs Brazil", "date": "1930-07-14",
             "stage": "group stage", "group": "Group 2", "tournament_id": "T-2",
             "teams": ["Brazil", "Yugoslavia"]},
        ],
        "players": [{"player_id": "P-1", "name": "André Maschinot",
                     "teams": ["France"]},
                    {"player_id": "P-2", "name": "Ivan Bek",
                     "teams": ["Yugoslavia"]}],
        "teams": [{"team_id": "T-30", "name": "France", "code": "FRA"}],
        "tournaments": [{"tournament_id": "T-1", "name": "1930 Cup"},
                        {"tournament_id": "T-2", "name": "1931 Cup"}],
        "period_windows": {},
    })


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------

def test_sentences_split_without_a_space_after_the_full_stop():
    """The corpus runs sentences together, so '. ' alone would return one long run."""
    text = "A scored in minute 3.In B vs C on 1 May 1990, D (E) scored in minute 9."
    assert len(split_sentences(text)) == 2


@pytest.mark.parametrize("sentence,pattern,minute", [
    ("André Maschinot scored for France in the 43rd minute of France vs Mexico "
     "at the 1930 Cup.", "scored_for", 43),
    ("In France vs Mexico on 13 July 1930, André Maschinot (France) scored in "
     "minute 43 during the first half.", "in_match_on_date", 43),
    ("At the 1930 Cup group stage, France scored through André Maschinot in "
     "minute 43.", "at_tournament_stage", 43),
    ("André Maschinot (France) scored in minute 43 during the first half.",
     "player_team_minute", 43),
])
def test_goal_patterns_extract(sentence, pattern, minute):
    claim = extract_claim(sentence)
    assert claim is not None and claim.pattern == pattern
    assert claim.kind == GOAL and claim.minute == minute


def test_knockout_stage_containing_digits_is_extracted():
    """'round of 16' was previously lost to an alphabetic-only character class."""
    claim = extract_claim("At the 1934 Cup round of 16, Egypt scored through "
                          "Abdullah Al-Buloushi in minute 59.")
    assert claim is not None and claim.minute == 59 and claim.team == "Egypt"


def test_total_patterns_extract():
    a = extract_claim("Dennis Wilshaw scored 1 goal at the 1954 Cup.")
    assert a is not None and a.kind == PLAYER_TOTAL and a.count == 1
    b = extract_claim("France vs Mexico at the 1930 Cup on 13 July 1930 produced "
                      "4 goals: France 3, Mexico 1.")
    assert b is not None and b.kind == MATCH_TOTAL and b.count == 4


def test_garbled_sentence_yields_no_claim():
    assert extract_claim("In Netherlands vs Slovakia on 28 June 2010, Slovakia) "
                         "scored in minute 24 during the first half.") is None
    assert extract_claim("Stefan Dobay in minute 35.") is None


def test_unparseable_text_is_not_checkable(graph):
    verdict = verify(extract_claim("the ball went in somehow."), graph)
    assert verdict.status == NOT_CHECKABLE


# --------------------------------------------------------------------------
# rule checks, which need no lookup
# --------------------------------------------------------------------------

def test_minute_outside_the_period_window_is_impossible():
    failures = check_rules(Claim(player="X", minute=68, period="first half"))
    assert failures and "first half" in failures[0]


def test_minute_inside_the_period_window_passes():
    assert check_rules(Claim(player="X", minute=68, period="second half")) == []


def test_minute_beyond_120_is_impossible():
    assert check_rules(Claim(player="X", minute=131)) != []


def test_team_not_in_the_fixture_is_impossible():
    failures = check_rules(Claim(player="X", minute=10, team="Brazil",
                                 match="France vs Mexico"))
    assert failures and "did not play" in failures[0]


def test_rule_failure_contradicts_without_consulting_the_graph(graph):
    """A self-inconsistent claim is refuted by logic alone."""
    claim = extract_claim("André Maschinot (France) scored in minute 68 during "
                          "the first half.")
    verdict = verify(claim, graph)
    assert verdict.status == CONTRADICTED and verdict.rule_failures


# --------------------------------------------------------------------------
# graph checks
# --------------------------------------------------------------------------

def test_recorded_goal_is_supported(graph):
    claim = extract_claim("André Maschinot scored for France in the 43rd minute "
                          "of France vs Mexico at the 1930 Cup.")
    verdict = verify(claim, graph)
    assert verdict.status == SUPPORTED and verdict.matched_goal_id == "G-1"


def test_accents_do_not_decide_the_verdict(graph):
    """The model drops diacritics; that is spelling, not falsehood."""
    claim = extract_claim("Andre Maschinot scored for France in the 43rd minute "
                          "of France vs Mexico at the 1930 Cup.")
    assert verify(claim, graph).status == SUPPORTED


def test_wrong_minute_is_contradicted_and_reports_the_real_ones(graph):
    claim = extract_claim("André Maschinot scored for France in the 12th minute "
                          "of France vs Mexico at the 1930 Cup.")
    verdict = verify(claim, graph)
    assert verdict.status == CONTRADICTED
    assert "43" in verdict.reasons[0]


def test_right_goal_wrong_tournament_is_contradicted(graph):
    """The commonest confabulation: a real goal relocated to another tournament."""
    claim = extract_claim("At the 1931 Cup group stage, France scored through "
                          "André Maschinot in minute 43.")
    assert verify(claim, graph).status == CONTRADICTED


def test_player_who_did_not_score_in_that_match_is_contradicted(graph):
    claim = extract_claim("Ivan Bek scored for Yugoslavia in the 30th minute of "
                          "France vs Mexico at the 1930 Cup.")
    assert verify(claim, graph).status == CONTRADICTED


def test_unknown_player_is_unverified_not_contradicted(graph):
    """Absence of a record is not evidence the claim is false."""
    claim = extract_claim("Raf Mannion scored for France in the 43rd minute of "
                          "France vs Mexico at the 1930 Cup.")
    verdict = verify(claim, graph)
    assert verdict.status == UNVERIFIED


def test_unknown_match_is_unverified_not_contradicted(graph):
    claim = extract_claim("André Maschinot scored for France in the 43rd minute "
                          "of France vs Peru at the 1930 Cup.")
    assert verify(claim, graph).status == UNVERIFIED


# --------------------------------------------------------------------------
# totals
# --------------------------------------------------------------------------

def test_correct_player_total_is_supported(graph):
    assert verify(extract_claim("André Maschinot scored 2 goals at the 1930 Cup."),
                  graph).status == SUPPORTED


def test_wrong_player_total_is_contradicted_with_the_real_count(graph):
    verdict = verify(extract_claim("André Maschinot scored 5 goals at the 1930 Cup."),
                     graph)
    assert verdict.status == CONTRADICTED and "2" in verdict.reasons[0]


def test_unknown_player_total_is_unverified(graph):
    assert verify(extract_claim("Raf Mannion scored 1 goal at the 1930 Cup."),
                  graph).status == UNVERIFIED


def test_correct_match_total_is_supported(graph):
    verdict = verify(extract_claim("France vs Mexico at the 1930 Cup on 13 July 1930 "
                                   "produced 2 goals: France 2."), graph)
    assert verdict.status == SUPPORTED


def test_wrong_match_total_is_contradicted(graph):
    verdict = verify(extract_claim("France vs Mexico at the 1930 Cup on 13 July 1930 "
                                   "produced 7 goals: France 7."), graph)
    assert verdict.status == CONTRADICTED and "2" in verdict.reasons[0]


# --------------------------------------------------------------------------
# the release gate
# --------------------------------------------------------------------------

def test_gate_releases_only_supported_claims(graph):
    text = ("André Maschinot scored for France in the 43rd minute of France vs "
            "Mexico at the 1930 Cup."
            "Raf Mannion scored for France in the 12th minute of France vs Mexico "
            "at the 1930 Cup.")
    verdicts = [verify(c, graph) for _, c in extract_claims(text)]
    released, withheld = gate(verdicts)
    assert [v.status for v in released] == [SUPPORTED]
    # UNVERIFIED is withheld too: not known to be false, but not shown to be true.
    assert withheld and all(v.status != SUPPORTED for v in withheld)
