"""Turn the World Cup goal records into everything the experiment needs.

One structured dataset serves three roles, which is what makes the comparison honest:

  1. **Training corpus** - goals verbalised into sentences, written as nanoGPT's
     `train.bin` / `val.bin` / `meta.pkl`.
  2. **Knowledge graph** - the same rows as typed facts, which is what the symbolic
     layer checks generated claims against.
  3. **Evaluation set** - questions with oracle answers, taken from matches the model
     never saw.

The held-out split is by **match**, not by row. If a match's other goals were in the
training corpus the model could recite a neighbouring fact and look correct, so whole
matches are withheld. This is what creates genuine opportunities to hallucinate: the
model is asked about events it has no information on.

    python scripts/prepare_football.py --csv C:/Users/aprav/Downloads/goal.txt

Writes under `nanogpt/data/football/` (the corpus) and `artifacts/football/` (the
knowledge graph, the evaluation set and the manifest).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pickle
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# Periods and the minute windows they permit. Taken from the data and verified against
# it in `verify_period_minutes` - not assumed.
PERIOD_WINDOWS = {
    "first half": (1, 45),
    "second half": (46, 90),
    "first half, stoppage time": (45, 45),
    "second half, stoppage time": (90, 90),
    "extra time, first half": (91, 105),
    "extra time, second half": (106, 120),
    "extra time, first half, stoppage time": (105, 105),
    "extra time, second half, stoppage time": (120, 120),
}

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def player_name(row: dict) -> str:
    """Join given and family name, honouring the dataset's 'not applicable' marker.

    Many Brazilian players are recorded by a single name (Leonidas, Ronaldo, Marta),
    with 'not applicable' in the given-name column. Pasting that in would teach the
    model a phrase that means nothing.
    """
    given = (row.get("given_name") or "").strip()
    family = (row.get("family_name") or "").strip()
    if given and given.lower() != "not applicable":
        return f"{given} {family}".strip()
    return family


def pretty_date(iso: str) -> str:
    try:
        y, m, d = iso.split("-")
        return f"{int(d)} {MONTHS[int(m) - 1]} {y}"
    except Exception:
        return iso


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# --------------------------------------------------------------------------
# verbalisation
# --------------------------------------------------------------------------

def goal_sentences(row: dict) -> list[str]:
    """Several phrasings of one goal.

    More than one, for two reasons: the corpus is small and needs the volume, and a
    model trained on a single template learns the template rather than the fact. The
    phrasings state the same facts, so none of them teaches anything false.
    """
    name = player_name(row)
    team = row["player_team_name"]
    minute = row["minute_regulation"]
    period = row["match_period"]
    match = row["match_name"]
    tournament = row["tournament_name"]
    date = pretty_date(row["match_date"])
    stage = row["stage_name"]
    credited = row["team_name"]

    out = [
        f"{name} scored for {credited} in the {ordinal(int(minute))} minute of "
        f"{match} at the {tournament}.",
        f"In {match} on {date}, {name} ({team}) scored in minute {minute} "
        f"during the {period}.",
        f"At the {tournament} {stage}, {credited} scored through {name} "
        f"in minute {minute}.",
    ]
    if row["penalty"] == "1":
        out.append(f"{name} converted a penalty for {credited} in the "
                   f"{ordinal(int(minute))} minute of {match}.")
    if row["own_goal"] == "1":
        out.append(f"{name} of {team} scored an own goal in minute {minute} of "
                   f"{match}, credited to {credited}.")
    return out


def aggregate_sentences(rows: list[dict]) -> list[str]:
    """Totals the model could otherwise only guess at."""
    out: list[str] = []
    per_player_tournament: dict[tuple, int] = defaultdict(int)
    per_match: dict[str, list[dict]] = defaultdict(list)
    per_player: dict[str, int] = defaultdict(int)
    names: dict[str, str] = {}

    for r in rows:
        per_player_tournament[(r["player_id"], r["tournament_id"])] += 1
        per_match[r["match_id"]].append(r)
        per_player[r["player_id"]] += 1
        names[r["player_id"]] = player_name(r)

    for (pid, tid), count in per_player_tournament.items():
        sample = next(r for r in rows if r["player_id"] == pid and r["tournament_id"] == tid)
        out.append(f"{names[pid]} scored {count} goal{'s' if count != 1 else ''} "
                   f"at the {sample['tournament_name']}.")
    for pid, count in per_player.items():
        if count >= 3:
            out.append(f"{names[pid]} scored {count} goals in FIFA World Cup "
                       f"tournaments in this record.")
    for mid, goals in per_match.items():
        g = goals[0]
        tally: dict[str, int] = defaultdict(int)
        for r in goals:
            tally[r["team_name"]] += 1
        score = ", ".join(f"{t} {c}" for t, c in sorted(tally.items()))
        out.append(f"{g['match_name']} at the {g['tournament_name']} on "
                   f"{pretty_date(g['match_date'])} produced {len(goals)} "
                   f"goal{'s' if len(goals) != 1 else ''}: {score}.")
    return out


# --------------------------------------------------------------------------
# knowledge graph and rules
# --------------------------------------------------------------------------

def build_graph(rows: list[dict]) -> dict:
    """Typed facts the verifier checks claims against. Built from every row.

    The graph deliberately includes the held-out matches: it is ground truth, not the
    model's knowledge. A claim about a withheld match must still be judged correctly.
    """
    goals, matches, players, teams, tournaments = [], {}, {}, {}, {}
    for r in rows:
        goals.append({
            "goal_id": r["goal_id"], "match_id": r["match_id"],
            "tournament_id": r["tournament_id"],
            "player_id": r["player_id"], "player_name": player_name(r),
            "player_team": r["player_team_name"],
            "credited_team": r["team_name"],
            "minute": int(r["minute_regulation"]) if r["minute_regulation"].isdigit() else None,
            "stoppage": int(r["minute_stoppage"]) if r["minute_stoppage"].isdigit() else 0,
            "period": r["match_period"],
            "own_goal": r["own_goal"] == "1", "penalty": r["penalty"] == "1",
        })
        matches.setdefault(r["match_id"], {
            "match_id": r["match_id"], "name": r["match_name"],
            "date": r["match_date"], "stage": r["stage_name"],
            "group": r["group_name"], "tournament_id": r["tournament_id"],
            "teams": set(),
        })["teams"].add(r["player_team_name"])
        players.setdefault(r["player_id"], {
            "player_id": r["player_id"], "name": player_name(r),
            "teams": set(),
        })["teams"].add(r["player_team_name"])
        teams.setdefault(r["team_id"], {"team_id": r["team_id"], "name": r["team_name"],
                                        "code": r["team_code"]})
        tournaments.setdefault(r["tournament_id"], {
            "tournament_id": r["tournament_id"], "name": r["tournament_name"]})

    for m in matches.values():
        # The match name carries both sides even when one failed to score, so it is a
        # better source for the teams that played than the goal rows alone.
        parts = [p.strip() for p in m["name"].split(" vs ")]
        m["teams"] = sorted(set(parts) | m["teams"]) if len(parts) == 2 else sorted(m["teams"])
    for p in players.values():
        p["teams"] = sorted(p["teams"])

    return {"goals": goals, "matches": list(matches.values()),
            "players": list(players.values()), "teams": list(teams.values()),
            "tournaments": list(tournaments.values()),
            "period_windows": PERIOD_WINDOWS}


def verify_period_minutes(rows: list[dict]) -> list[str]:
    """Check the minute windows against the data instead of trusting them.

    A rule the dataset violates is not a rule, and encoding one would make the verifier
    reject true claims.
    """
    problems = []
    for r in rows:
        window = PERIOD_WINDOWS.get(r["match_period"])
        minute = r["minute_regulation"]
        if window is None:
            problems.append(f"unknown period {r['match_period']!r} ({r['goal_id']})")
            continue
        if not minute.isdigit():
            continue
        lo, hi = window
        if not (lo <= int(minute) <= hi):
            problems.append(f"{r['goal_id']}: {r['match_period']} minute {minute} "
                            f"outside [{lo},{hi}]")
    return problems


# --------------------------------------------------------------------------
# evaluation questions, with oracle answers
# --------------------------------------------------------------------------

def build_eval(rows: list[dict], graph: dict) -> list[dict]:
    """Questions whose answers the training corpus never contained."""
    by_match: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_match[r["match_id"]].append(r)

    cases = []
    for mid, goals in sorted(by_match.items()):
        g = sorted(goals, key=lambda r: int(r["minute_regulation"] or 0))[0]
        name = player_name(g)
        cases.append({
            "id": f"{mid}-first-scorer", "category": "first_scorer",
            "question": f"Who scored the first goal in {g['match_name']} at the "
                        f"{g['tournament_name']}?",
            "gold": {"kind": "player", "value": name,
                     "evidence_goal_id": g["goal_id"]},
        })
        cases.append({
            "id": f"{mid}-minute", "category": "minute",
            "question": f"In which minute did {name} score in {g['match_name']} at the "
                        f"{g['tournament_name']}?",
            "gold": {"kind": "minute", "value": int(g["minute_regulation"]),
                     "evidence_goal_id": g["goal_id"]},
        })
        cases.append({
            "id": f"{mid}-count", "category": "goal_count",
            "question": f"How many goals were scored in {g['match_name']} at the "
                        f"{g['tournament_name']}?",
            "gold": {"kind": "count", "value": len(goals), "match_id": mid},
        })
    return cases


# --------------------------------------------------------------------------
# tokenisation
# --------------------------------------------------------------------------

def train_tokenizer(texts: list[str], vocab_size: int, out_dir: Path) -> Path:
    from tokenizers import Tokenizer, decoders, pre_tokenizers, trainers
    from tokenizers.models import BPE

    tok = Tokenizer(BPE(unk_token=None))
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.decoder = decoders.ByteLevel()
    trainer = trainers.BpeTrainer(
        vocab_size=vocab_size, special_tokens=["<|pad|>", "<|endoftext|>"],
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(), show_progress=False)
    tok.train_from_iterator(texts, trainer=trainer)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "tokenizer.json"
    tok.save(str(path))
    return path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--data-dir", type=Path, default=ROOT / "nanogpt/data/football")
    ap.add_argument("--artifacts", type=Path, default=ROOT / "artifacts/football")
    ap.add_argument("--vocab-size", type=int, default=4096,
                    help="small, because the corpus is small")
    ap.add_argument("--holdout-fraction", type=float, default=0.12,
                    help="share of MATCHES withheld entirely from training")
    ap.add_argument("--val-fraction", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=1337)
    args = ap.parse_args()

    rows = list(csv.DictReader(args.csv.open(encoding="utf-8")))
    print(f"read {len(rows)} goal records from {args.csv}")

    problems = verify_period_minutes(rows)
    if problems:
        print(f"WARNING: {len(problems)} rows violate the period/minute windows:")
        for p in problems[:5]:
            print("   ", p)
        print("    The minute rule would reject true claims; fix PERIOD_WINDOWS first.")
    else:
        print("period/minute windows hold for every row - safe to use as a rule")

    # ---- hold out whole matches ----
    match_ids = sorted({r["match_id"] for r in rows})
    rng = random.Random(args.seed)
    rng.shuffle(match_ids)
    n_hold = max(1, int(len(match_ids) * args.holdout_fraction))
    heldout = set(match_ids[:n_hold])
    train_rows = [r for r in rows if r["match_id"] not in heldout]
    held_rows = [r for r in rows if r["match_id"] in heldout]
    print(f"matches: {len(match_ids)} total, {len(heldout)} held out "
          f"({len(held_rows)} goals withheld, {len(train_rows)} for training)")

    # ---- corpus ----
    sentences: list[str] = []
    for r in train_rows:
        sentences.extend(goal_sentences(r))
    sentences.extend(aggregate_sentences(train_rows))
    rng.shuffle(sentences)
    print(f"corpus: {len(sentences)} sentences, "
          f"{sum(len(s) for s in sentences)/1e6:.2f} MB of text")

    split = int(len(sentences) * (1 - args.val_fraction))
    train_text, val_text = sentences[:split], sentences[split:]

    # ---- what the evaluation may safely ask about ----
    # A fixture name alone does not identify a match: Argentina vs Brazil recurs across
    # tournaments, and the 1934 and 1938 tournaments contained replays, so even
    # (fixture, tournament) can name two different games. A question is only answerable
    # without ambiguity when its (fixture, tournament) pair maps to exactly one match
    # AND never appeared in training.
    pair_to_matches: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in rows:
        pair_to_matches[(r["match_name"], r["tournament_name"])].add(r["match_id"])
    train_pairs = {(r["match_name"], r["tournament_name"]) for r in train_rows}

    def askable(match_id: str) -> bool:
        sample = next(r for r in held_rows if r["match_id"] == match_id)
        pair = (sample["match_name"], sample["tournament_name"])
        return len(pair_to_matches[pair]) == 1 and pair not in train_pairs

    eval_matches = {m for m in heldout if askable(m)}
    dropped = len(heldout) - len(eval_matches)
    print(f"evaluation-safe matches: {len(eval_matches)} of {len(heldout)} held out "
          f"({dropped} dropped as ambiguous or fixture-shared with training)")
    if not eval_matches:
        sys.exit("no unambiguous held-out matches; raise --holdout-fraction")
    held_rows = [r for r in held_rows if r["match_id"] in eval_matches]

    # The invariant that matters: no evaluated (fixture, tournament) pair occurs in
    # training. A bare fixture name shared with training is harmless, because every
    # question names the tournament as well — and it is in fact useful, since a model
    # that answers from the wrong tournament's match is hallucinating in exactly the
    # way this study is built to detect.
    remaining = sorted({(r["match_name"], r["tournament_name"]) for r in held_rows})
    leaked = sorted(p for p in remaining if p in train_pairs)
    shared_name = sorted({a for a, _ in remaining
                          if any(a == b for b, _ in train_pairs)})
    print(f"leakage check: {len(remaining)} evaluated fixtures, {len(leaked)} leaked, "
          f"{len(shared_name)} share a bare fixture name with training "
          f"(disambiguated by tournament in every question)")
    if leaked:
        sys.exit(f"aborting: {len(leaked)} evaluated (fixture, tournament) pairs "
                 f"appear in training")

    # ---- tokenise ----
    args.data_dir.mkdir(parents=True, exist_ok=True)
    tok_path = train_tokenizer(train_text, args.vocab_size, args.data_dir)
    from tokenizers import Tokenizer
    tok = Tokenizer.from_file(str(tok_path))
    eot = tok.token_to_id("<|endoftext|>")

    counts = {}
    for name, texts in (("train", train_text), ("val", val_text)):
        ids: list[int] = []
        for enc in tok.encode_batch(texts):
            ids.extend(enc.ids)
            ids.append(eot)
        np.asarray(ids, dtype=np.uint16).tofile(args.data_dir / f"{name}.bin")
        counts[name] = len(ids)
        print(f"  {name}.bin: {len(ids):,} tokens")

    with (args.data_dir / "meta.pkl").open("wb") as fh:
        pickle.dump({"vocab_size": tok.get_vocab_size(),
                     "tokenizer": "tokenizer.json",
                     "note": "byte-level BPE trained on the football training split "
                             "only; decode with tokenizers, not tiktoken"}, fh)

    # ---- graph, evaluation set, manifest ----
    args.artifacts.mkdir(parents=True, exist_ok=True)
    graph = build_graph(rows)
    (args.artifacts / "knowledge_graph.json").write_text(
        json.dumps(graph, indent=1, default=str), encoding="utf-8")
    cases = build_eval(held_rows, graph)
    with (args.artifacts / "eval.jsonl").open("w", encoding="utf-8") as fh:
        for case in cases:
            fh.write(json.dumps(case) + "\n")
    print(f"knowledge graph: {len(graph['goals'])} goals, {len(graph['matches'])} "
          f"matches, {len(graph['players'])} players")
    print(f"evaluation: {len(cases)} questions from held-out matches only")

    manifest = {
        "source": {"file": str(args.csv), "sha256": sha256(args.csv),
                   "rows": len(rows),
                   "description": "FIFA World Cup goal records, 1930-2022, "
                                  "men's and women's tournaments"},
        "split": {"method": "by match, so no held-out match has sibling goals in "
                            "training", "seed": args.seed,
                  "matches_total": len(match_ids), "matches_heldout": len(heldout),
                  "goals_train": len(train_rows), "goals_heldout": len(held_rows)},
        "corpus": {"sentences": len(sentences),
                   "characters": sum(len(s) for s in sentences),
                   "tokens": counts,
                   "vocab_size": tok.get_vocab_size(),
                   "tokenizer_sha256": sha256(tok_path)},
        "leakage_check": {"evaluated_fixtures": len(remaining),
                          "ambiguous_or_shared_dropped": dropped,
                          "method": "a fixture is evaluated only when its "
                                    "(match, tournament) pair maps to exactly one "
                                    "match and never appeared in training",
                          "passed": True},
        "rules_verified": {"period_minute_windows": not problems,
                           "violations": len(problems)},
        "evaluation": {"cases": len(cases),
                       "categories": sorted({c["category"] for c in cases})},
        "artifacts": {name: {"tokens": counts[name],
                             "sha256": sha256(args.data_dir / f"{name}.bin")}
                      for name in counts},
    }
    (args.artifacts / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                                  encoding="utf-8")
    print(f"\nwrote {args.data_dir} and {args.artifacts}")

    total = counts["train"]
    print(f"\nSizing note: {total:,} training tokens. A 29.4M-parameter model is "
          f"{29.4e6/total:.0f}x over-parameterised for this corpus and will memorise "
          f"it. See the report for the model size this supports.")


if __name__ == "__main__":
    main()
