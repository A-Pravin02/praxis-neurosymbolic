"""The neuro-symbolic layer over the from-scratch World Cup model.

The model is a base language model: it continues text, it does not answer questions,
and nothing in its training stops it inventing a goal that was never scored. This
module takes what it writes, turns each sentence into a typed claim, and decides
whether the knowledge graph supports it.

Two kinds of check run, and keeping them apart is the point of the design:

  * **Rule checks** need no lookup. A goal in the 'first half' at minute 68 is
    impossible whatever the records say, because the period determines the window.
    These catch internally incoherent claims.
  * **Graph checks** ask whether this goal is on record: did that player score in that
    match, in that minute, for that team.

Statuses are four-valued, following the architecture used elsewhere in PRAXIS:

  SUPPORTED      the graph records this goal
  CONTRADICTED   the graph records something incompatible with it
  UNVERIFIED     the claim is well-formed and checkable, but no record matches and
                 none contradicts it - typically an invented match
  NOT_CHECKABLE  no claim could be extracted from the sentence

Missing evidence is never reported as refutation. A claim about a match the graph has
never heard of is UNVERIFIED, not CONTRADICTED, because the graph is not complete and
saying otherwise would be a different error from the one being measured.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from pathlib import Path

# Minute windows implied by the match period. Verified against every row of the source
# data by `scripts/prepare_football.py` before being used as a rule.
PERIOD_WINDOWS: dict[str, tuple[int, int]] = {
    "first half": (1, 45),
    "second half": (46, 90),
    "first half, stoppage time": (45, 45),
    "second half, stoppage time": (90, 90),
    "extra time, first half": (91, 105),
    "extra time, second half": (106, 120),
    "extra time, first half, stoppage time": (105, 105),
    "extra time, second half, stoppage time": (120, 120),
}

SUPPORTED = "SUPPORTED"
CONTRADICTED = "CONTRADICTED"
UNVERIFIED = "UNVERIFIED"
NOT_CHECKABLE = "NOT_CHECKABLE"


# What a sentence asserts. Goal events and totals need different checks, and conflating
# them would mean scoring a count claim against a single goal record.
GOAL = "goal"
PLAYER_TOTAL = "player_tournament_total"
MATCH_TOTAL = "match_total"


@dataclass
class Claim:
    """One assertion made by the model: a goal event, or a total."""
    kind: str = GOAL
    player: str | None = None
    team: str | None = None            # the team credited with the goal
    minute: int | None = None
    match: str | None = None
    tournament: str | None = None
    period: str | None = None
    count: int | None = None           # for the total kinds
    pattern: str = ""
    sentence: str = ""

    def is_checkable(self) -> bool:
        if self.kind == GOAL:
            # Without a player and a minute there is nothing specific to look up.
            return self.player is not None and self.minute is not None
        if self.kind == PLAYER_TOTAL:
            return self.player is not None and self.count is not None
        if self.kind == MATCH_TOTAL:
            return self.match is not None and self.count is not None
        return False


@dataclass
class Verdict:
    status: str
    claim: Claim
    reasons: list[str] = field(default_factory=list)
    rule_failures: list[str] = field(default_factory=list)
    matched_goal_id: str | None = None

    def as_dict(self) -> dict:
        out = asdict(self)
        out["claim"] = asdict(self.claim)
        return out


# --------------------------------------------------------------------------
# claim extraction
# --------------------------------------------------------------------------

# The generator was trained on these three phrasings plus two variants, so extraction
# targets them directly. A sentence matching none of them yields no claim, which is
# reported as NOT_CHECKABLE rather than guessed at.
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("scored_for", re.compile(
        r"^(?P<player>.+?) scored for (?P<team>.+?) in the (?P<minute>\d+)(?:st|nd|rd|th) "
        r"minute of (?P<match>.+?) at the (?P<tournament>.+?)\.", re.S)),
    ("in_match_on_date", re.compile(
        r"^In (?P<match>.+?) on (?P<date>[^,]+), (?P<player>.+?) \((?P<team>.+?)\) "
        r"scored in minute (?P<minute>\d+) during the (?P<period>.+?)\.", re.S)),
    # The stage may contain digits ("round of 16"), which an alphabetic-only class
    # silently rejected, losing every sentence about a knockout round.
    ("at_tournament_stage", re.compile(
        r"^At the (?P<tournament>.+?) (?P<stage>[a-z0-9\- ]+?), (?P<team>.+?) scored "
        r"through (?P<player>.+?) in minute (?P<minute>\d+)\.", re.S)),
    # The model often drops the "In {match} on {date}," prefix and starts at the
    # player. Same assertion, minus the match, so it is still checkable.
    ("player_team_minute", re.compile(
        r"^(?P<player>.+?) \((?P<team>.+?)\) scored in minute (?P<minute>\d+) "
        r"during the (?P<period>.+?)\.", re.S)),
    ("penalty", re.compile(
        r"^(?P<player>.+?) converted a penalty for (?P<team>.+?) in the "
        r"(?P<minute>\d+)(?:st|nd|rd|th) minute of (?P<match>.+?)\.", re.S)),
    ("own_goal", re.compile(
        r"^(?P<player>.+?) of (?P<team>.+?) scored an own goal in minute "
        r"(?P<minute>\d+) of (?P<match>.+?), credited to (?P<credited>.+?)\.", re.S)),
]

# Totals. Checked by counting records rather than matching one, so they are kept
# separate from the goal patterns above.
TOTAL_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("player_total_tournament", PLAYER_TOTAL, re.compile(
        r"^(?P<player>.+?) scored (?P<count>\d+) goals? at the (?P<tournament>.+?)\.",
        re.S)),
    ("player_total_overall", PLAYER_TOTAL, re.compile(
        r"^(?P<player>.+?) scored (?P<count>\d+) goals? in FIFA World Cup "
        r"tournaments[^.]*\.", re.S)),
    ("match_total", MATCH_TOTAL, re.compile(
        r"^(?P<match>.+?) at the (?P<tournament>.+?) on [^,]+? produced "
        r"(?P<count>\d+) goals?:", re.S)),
]


def split_sentences(text: str) -> list[str]:
    """Split generated text into sentences.

    The corpus has no space after a full stop between sentences, so the model emits
    'minute 68.In France vs ...'. Splitting on '. ' alone would return one long run.
    """
    text = text.replace("\n", " ")
    parts = re.split(r"(?<=\.)(?=[A-Z0-9])|(?<=\.)\s+", text)
    return [p.strip() for p in parts if p.strip()]


def extract_claim(sentence: str) -> Claim | None:
    text = sentence.strip()

    # Totals first: "X scored 3 goals at the 1954 ..." also matches nothing in the goal
    # patterns, but checking totals first keeps the intent unambiguous.
    for name, kind, pattern in TOTAL_PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        g = m.groupdict()
        return Claim(
            kind=kind,
            player=(g.get("player") or "").strip() or None,
            match=(g.get("match") or "").strip() or None,
            tournament=(g.get("tournament") or "").strip() or None,
            count=int(g["count"]),
            pattern=name,
            sentence=text,
        )

    for name, pattern in PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        g = m.groupdict()
        credited = g.get("credited") or g.get("team")
        return Claim(
            player=(g.get("player") or "").strip() or None,
            team=(credited or "").strip() or None,
            minute=int(g["minute"]) if g.get("minute") else None,
            match=(g.get("match") or "").strip() or None,
            tournament=(g.get("tournament") or "").strip() or None,
            period=(g.get("period") or "").strip() or None,
            pattern=name,
            sentence=sentence.strip(),
        )
    return None


def extract_claims(text: str) -> list[tuple[str, Claim | None]]:
    return [(s, extract_claim(s)) for s in split_sentences(text)]


# --------------------------------------------------------------------------
# the knowledge graph
# --------------------------------------------------------------------------

def _norm(value: str | None) -> str:
    """Casefold and strip accents so 'Andre' matches 'André'.

    The model frequently drops diacritics. Treating that as a factual error would
    measure its spelling rather than its truthfulness.
    """
    if not value:
        return ""
    stripped = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped).strip().casefold()


class FootballGraph:
    """Indexed view of the goal records."""

    def __init__(self, payload: dict) -> None:
        self.goals = payload["goals"]
        self.matches = {m["match_id"]: m for m in payload["matches"]}
        self.tournaments = {t["tournament_id"]: t for t in payload["tournaments"]}

        self._by_player: dict[str, list[dict]] = {}
        for goal in self.goals:
            self._by_player.setdefault(_norm(goal["player_name"]), []).append(goal)

        self._match_key: dict[tuple[str, str], list[str]] = {}
        for match in payload["matches"]:
            tournament = self.tournaments.get(match["tournament_id"], {}).get("name", "")
            self._match_key.setdefault((_norm(match["name"]), _norm(tournament)),
                                       []).append(match["match_id"])
        self._known_players = set(self._by_player)
        self._known_matches = {name for name, _ in self._match_key}

    @classmethod
    def load(cls, path: Path) -> "FootballGraph":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def goals_by_player(self, player: str) -> list[dict]:
        return self._by_player.get(_norm(player), [])

    def knows_player(self, player: str) -> bool:
        return _norm(player) in self._known_players

    def knows_match(self, match: str) -> bool:
        return _norm(match) in self._known_matches

    def match_ids(self, match: str, tournament: str | None) -> list[str]:
        if tournament:
            ids = self._match_key.get((_norm(match), _norm(tournament)))
            if ids:
                return ids
        return [mid for (name, _), ids in self._match_key.items()
                if name == _norm(match) for mid in ids]

    def tournament_ids(self, name: str) -> set[str]:
        return {tid for tid, t in self.tournaments.items()
                if _norm(t.get("name")) == _norm(name)}

    def tournament_of(self, match_id: str) -> str:
        match = self.matches.get(match_id, {})
        return self.tournaments.get(match.get("tournament_id"), {}).get("name", "")


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

def check_rules(claim: Claim) -> list[str]:
    """Checks needing no lookup. A failure here means the claim is self-inconsistent."""
    failures = []
    if claim.minute is not None and not (1 <= claim.minute <= 120):
        failures.append(f"minute {claim.minute} outside the possible range 1-120")
    if claim.period and claim.minute is not None:
        window = PERIOD_WINDOWS.get(claim.period.strip().rstrip("."))
        if window is None:
            failures.append(f"unknown match period {claim.period!r}")
        elif not (window[0] <= claim.minute <= window[1]):
            failures.append(
                f"minute {claim.minute} is impossible during the {claim.period}: "
                f"that period spans minutes {window[0]}-{window[1]}")
    if claim.match and " vs " in claim.match and claim.team:
        sides = [_norm(s) for s in claim.match.split(" vs ")]
        if _norm(claim.team) not in sides:
            failures.append(
                f"{claim.team} is credited with the goal but did not play in "
                f"{claim.match}")
    return failures


def verify_player_total(claim: Claim, graph: FootballGraph) -> Verdict:
    """Count the player's recorded goals and compare.

    Own goals are excluded from a player's tally: the corpus credits them to the
    opposing team, so counting them here would contradict how the model was taught.
    """
    goals = [g for g in graph.goals_by_player(claim.player) if not g["own_goal"]]
    if not goals and not graph.knows_player(claim.player):
        return Verdict(UNVERIFIED, claim,
                       [f"no player called {claim.player} is on record"])
    if claim.tournament:
        ids = graph.tournament_ids(claim.tournament)
        if not ids:
            return Verdict(UNVERIFIED, claim,
                           [f"no tournament called {claim.tournament} is on record"])
        goals = [g for g in goals if g["tournament_id"] in ids]
        scope = f"at the {claim.tournament}"
    else:
        scope = "in the records"
    actual = len(goals)
    if actual == claim.count:
        return Verdict(SUPPORTED, claim,
                       [f"{claim.player} has {actual} recorded goal(s) {scope}"])
    return Verdict(CONTRADICTED, claim,
                   [f"{claim.player} has {actual} recorded goal(s) {scope}, "
                    f"not {claim.count}"])


def verify_match_total(claim: Claim, graph: FootballGraph) -> Verdict:
    ids = graph.match_ids(claim.match, claim.tournament)
    if not ids:
        return Verdict(UNVERIFIED, claim,
                       [f"no match called {claim.match!r}"
                        + (f" at the {claim.tournament}" if claim.tournament else "")
                        + " is on record"])
    # A fixture that resolves to several matches (a replay) cannot be scored against
    # one total without guessing which game was meant.
    if len(ids) > 1:
        return Verdict(NOT_CHECKABLE, claim,
                       [f"{claim.match} at the {claim.tournament} names "
                        f"{len(ids)} different matches"])
    actual = sum(1 for g in graph.goals if g["match_id"] == ids[0])
    if actual == claim.count:
        return Verdict(SUPPORTED, claim,
                       [f"{claim.match} has {actual} recorded goal(s)"])
    return Verdict(CONTRADICTED, claim,
                   [f"{claim.match} has {actual} recorded goal(s), not {claim.count}"])


def verify(claim: Claim | None, graph: FootballGraph) -> Verdict:
    if claim is None or not claim.is_checkable():
        return Verdict(NOT_CHECKABLE, claim or Claim(),
                       ["no structured claim could be extracted"])

    if claim.kind == PLAYER_TOTAL:
        return verify_player_total(claim, graph)
    if claim.kind == MATCH_TOTAL:
        return verify_match_total(claim, graph)

    rule_failures = check_rules(claim)
    if rule_failures:
        # Self-inconsistent: no lookup can rescue it.
        return Verdict(CONTRADICTED, claim, list(rule_failures), rule_failures)

    candidates = graph.goals_by_player(claim.player)
    if not candidates:
        status = UNVERIFIED
        reason = (f"no goal by {claim.player} is on record"
                  if not graph.knows_player(claim.player)
                  else f"{claim.player} is known but has no recorded goals")
        return Verdict(status, claim, [reason])

    # Narrow by match when the claim names one.
    if claim.match:
        ids = set(graph.match_ids(claim.match, claim.tournament))
        if not ids:
            return Verdict(UNVERIFIED, claim,
                           [f"no match called {claim.match!r}"
                            + (f" at the {claim.tournament}" if claim.tournament else "")
                            + " is on record"])
        in_match = [g for g in candidates if g["match_id"] in ids]
        if not in_match:
            return Verdict(
                CONTRADICTED, claim,
                [f"{claim.player} did not score in {claim.match}"
                 + (f" at the {claim.tournament}" if claim.tournament else "")])
        candidates = in_match

    exact = [g for g in candidates if g["minute"] == claim.minute]
    if not exact:
        recorded = sorted({g["minute"] for g in candidates if g["minute"] is not None})
        return Verdict(
            CONTRADICTED, claim,
            [f"{claim.player} did not score in minute {claim.minute}"
             + (f" of {claim.match}" if claim.match else "")
             + (f"; recorded minute(s): {recorded}" if recorded else "")])

    goal = exact[0]
    reasons = [f"goal {goal['goal_id']}: {goal['player_name']} scored for "
               f"{goal['credited_team']} in minute {goal['minute']}"]

    if claim.team and _norm(claim.team) != _norm(goal["credited_team"]):
        return Verdict(CONTRADICTED, claim,
                       [f"the goal is credited to {goal['credited_team']}, "
                        f"not {claim.team}"], matched_goal_id=goal["goal_id"])
    if claim.tournament:
        actual = graph.tournament_of(goal["match_id"])
        if actual and _norm(claim.tournament) != _norm(actual):
            return Verdict(CONTRADICTED, claim,
                           [f"that goal was scored at the {actual}, "
                            f"not the {claim.tournament}"],
                           matched_goal_id=goal["goal_id"])

    return Verdict(SUPPORTED, claim, reasons, matched_goal_id=goal["goal_id"])


def verify_text(text: str, graph: FootballGraph) -> list[Verdict]:
    return [verify(claim, graph) for _, claim in extract_claims(text)]


def gate(verdicts: list[Verdict]) -> tuple[list[Verdict], list[Verdict]]:
    """Split into what may be released and what must be withheld.

    Only SUPPORTED claims are released. UNVERIFIED is withheld too: the system does not
    know it to be false, but it cannot show it to be true, and releasing it is what the
    architecture exists to prevent.
    """
    released = [v for v in verdicts if v.status == SUPPORTED]
    withheld = [v for v in verdicts if v.status != SUPPORTED]
    return released, withheld
