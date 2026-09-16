"""Symbolic fidelity: does the rule engine agree with the independent reference oracle?

For every record in every generated world, and every stated-fact variant the benchmark
uses, this evaluates the goal twice - once with the reasoner over the knowledge pack, once
with `app/evaluation/oracle.py` over the raw world - bypassing language understanding
entirely. Agreement measures whether the rules encode the policy text; it says nothing
about whether questions are understood, which the benchmark measures separately.

Every disagreement is printed with the deciding clause from each side, to be adjudicated
against the policy text. Neither implementation is assumed correct.

    python scripts/check_oracle.py            # all dev and test worlds
    python scripts/check_oracle.py --json out.json

Mutation analysis. Agreement alone cannot show the worlds are discriminating: two
implementations can agree on records that never reach the clause where they would
differ. So the check also runs *mutants* of the oracle - each a plausible misreading of
one clause (the newest policy version applied to every order, the Plus extension ignored,
an inclusive window made exclusive, ...) - and requires every mutant to be killed, that
is, to disagree with the reasoner on at least one record. A surviving mutant means the
benchmark cannot tell that misreading from the policy, and the generator is fixed.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.evaluation import oracle  # noqa: E402
from app.evaluation.worlds import DEV_SEEDS, TEST_SEEDS, generate_world, write_pack  # noqa: E402
from app.hybrid.knowledge_pack import load_pack  # noqa: E402
from app.symbolic.reasoner import Reasoner  # noqa: E402

REASONS = (None, "damaged_on_arrival", "change_of_mind")


def reasoner_gold(result, goal_kind: str):
    """Map a reasoner result onto the oracle's vocabulary."""
    if result.status in ("conditional", "unknown"):
        return ("conditional", None)
    value = result.value
    if isinstance(value, bool):
        return ("decision", value)
    if isinstance(value, (int, float)):
        return ("value", round(float(value), 2))
    return ("result", value)


def oracle_gold(gold: oracle.Gold):
    if gold.kind == "conditional":
        return ("conditional", None)
    if gold.kind == "value":
        return ("value", round(float(gold.value), 2))
    return (gold.kind, gold.value)


def check_world(seed: int) -> tuple[Counter, list[dict]]:
    world = generate_world(seed)
    pack = load_pack(str(write_pack(world)), "hashing")
    tally: Counter = Counter()
    disagreements: list[dict] = []

    def compare(goal: str, subject: str, stated: dict, gold: oracle.Gold, *, open_subject=False):
        reasoner = Reasoner(pack.program, pack.graph,
                            stated={(subject, k): v for k, v in stated.items()},
                            open_subjects={subject} if open_subject else set())
        got = reasoner_gold(reasoner.evaluate(subject, goal), "")
        want = oracle_gold(gold)
        ok = got == want
        tally[(goal, ok)] += 1
        if not ok:
            disagreements.append({"world": seed, "goal": goal, "subject": subject,
                                  "stated": stated, "oracle": want, "reasoner": got,
                                  "oracle_because": gold.because})

    for order in world.orders:
        status = order["status"]
        for reason in REASONS:
            stated = {"return_reason": reason} if reason else {}
            if status == "delivered":
                compare("return_allowed", order["id"], stated,
                        oracle.return_allowed(world, order, reason))
                compare("replacement_available", order["id"], stated,
                        oracle.replacement_available(world, order, reason))
            if reason is not None:
                compare("recommended_resolution", order["id"], stated,
                        oracle.recommended_resolution(world, order, reason))
                compare("approval_required", order["id"], stated,
                        oracle.approval_required(world, order, reason))
        if status != "payment_failed":
            compare("cancellation_allowed", order["id"], {},
                    oracle.cancellation_allowed(world, order))
        if status in ("placed", "packed"):
            compare("cancellation_refund", order["id"], {},
                    oracle.cancellation_refund(world, order))
        if status in ("placed", "packed", "shipped"):
            compare("free_delivery", order["id"], {}, oracle.free_delivery(world, order))
        if status in ("shipped", "out_for_delivery"):
            compare("delay_compensation", order["id"], {},
                    oracle.delay_compensation(world, order))
        if status == "payment_failed":
            compare("refund_required", order["id"], {}, oracle.refund_required(world, order))
        if status == "delivered":
            compare("goodwill_credit", order["id"], {}, oracle.goodwill_credit(world, order))
    for record in world.returns:
        compare("refund_approved", record["id"], {}, oracle.refund_approved(world, record))
    for employee in world.employees:
        compare("annual_leave_days", employee["id"], {},
                oracle.annual_leave_days(world, employee))
        compare("senior_leave_eligible", employee["id"], {},
                oracle.senior_leave_eligible(world, employee))
        for trip in _trips(world):
            stated = {"claimed_amount": trip["amount"], "travel_distance_km": trip["km"],
                      "travel_mode": trip["mode"], "receipts_attached": trip["receipts"],
                      "claim_submitted_after_days": trip["submitted_after_days"],
                      "claimant": employee["id"]}
            stated = {k: v for k, v in stated.items() if v is not None}
            compare("travel_claim_eligible", "case:claim", stated,
                    oracle.travel_claim_eligible(world, trip), open_subject=True)
            gold = oracle.reimbursable_amount(world, employee, trip)
            if gold.kind == "value":
                compare("reimbursable_amount", "case:claim", stated, gold, open_subject=True)
    return tally, disagreements


def _trips(world) -> list[dict]:
    p = world.params
    return [
        {"amount": p["receipt_threshold"] + 900, "km": p["min_trip_km"] + 40,
         "mode": "public_transport", "receipts": True, "submitted_after_days": 3},
        {"amount": p["receipt_threshold"] - 200, "km": p["min_trip_km"] + 5,
         "mode": "own_vehicle", "receipts": None, "submitted_after_days": 2},
        {"amount": p["receipt_threshold"] + 500, "km": p["min_trip_km"] - 10,
         "mode": "public_transport", "receipts": True, "submitted_after_days": 1},
        {"amount": p["receipt_threshold"] + 500, "km": p["min_trip_km"] + 10,
         "mode": "public_transport", "receipts": False, "submitted_after_days": 1},
        {"amount": world.grades["L3"] + 4000, "km": p["min_trip_km"] + 200,
         "mode": "own_vehicle", "receipts": True, "submitted_after_days": p["claim_deadline_days"]},
        {"amount": p["receipt_threshold"] + 100, "km": p["min_trip_km"] + 30,
         "mode": "public_transport", "receipts": True,
         "submitted_after_days": p["claim_deadline_days"] + 1},
    ]


MUTANTS = {
    "newest policy version for every order":
        ("governing_version", lambda orig: lambda w, o: w.versions[-1]),
    "oldest policy version for every order":
        ("governing_version", lambda orig: lambda w, o: w.versions[0]),
    "Plus extension ignored":
        ("return_window", lambda orig: lambda w, o: w.categories[
            w.product(o["product"])["category"]]["return_window_days"]),
    "return window exclusive":
        ("return_window", lambda orig: lambda w, o: orig(w, o) - 1),
    "stock ignored for replacements":
        ("replacement_available", lambda orig: lambda w, o, r: oracle.Gold(
            "decision", True) if r in oracle.DAMAGE_REASONS and oracle._damage_ok(w, o)
            else orig(w, o, r)),
    "cancellation fee charged to Plus members":
        ("cancellation_fee", lambda orig: lambda w, o: 0.0 if o["status"] == "placed" else min(
            o["total"] * w.params["cancellation_fee_rate"], w.params["cancellation_fee_cap"])),
    "delay threshold inclusive":
        ("delay_compensation", lambda orig: lambda w, o: orig(w, dict(
            o, promised_delivery=o["promised_delivery"] - __import__("datetime").timedelta(days=1)))),
    "goodwill without a recorded approval":
        ("goodwill_credit", lambda orig: lambda w, o: orig(w, dict(o, exception_approved=True))),
    "approval threshold ignored":
        ("approval_required", lambda orig: lambda w, o, r: oracle.Gold("decision", oracle
            .recommended_resolution(w, o, r).value in
            ("replacement", "full_refund", "return_and_refund_after_inspection"))),
    "receipts never required":
        ("travel_claim_eligible", lambda orig: lambda w, t: orig(w, dict(t, receipts=True))),
    "service years not checked":
        ("annual_leave_days", lambda orig: lambda w, e: oracle.Gold(
            "value", w.params["annual_leave_base"])),
    "inspection result ignored":
        ("refund_approved", lambda orig: lambda w, r: orig(w, dict(r, inspection="passed"))),
}


def mutation_analysis(seeds) -> dict[str, int]:
    killed: dict[str, int] = {}
    for label, (name, make) in MUTANTS.items():
        original = getattr(oracle, name)
        setattr(oracle, name, make(original))
        try:
            killed[label] = sum(len(check_world(seed)[1]) for seed in seeds)
        finally:
            setattr(oracle, name, original)
    return killed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json")
    parser.add_argument("--no-mutants", action="store_true")
    args = parser.parse_args()
    total: Counter = Counter()
    problems: list[dict] = []
    for seed in DEV_SEEDS + TEST_SEEDS:
        tally, found = check_world(seed)
        total.update(tally)
        problems.extend(found)
    goals = sorted({g for g, _ in total})
    agree = sum(v for (g, ok), v in total.items() if ok)
    count = sum(total.values())
    print(f"{'goal':28} agree / total")
    for goal in goals:
        print(f"{goal:28} {total[(goal, True)]:5} / {total[(goal, True)] + total[(goal, False)]}")
    print(f"{'ALL':28} {agree:5} / {count}  ({agree / count:.4f})")
    for problem in problems[:40]:
        print("DISAGREE", problem)
    survivors: list[str] = []
    mutants: dict[str, int] = {}
    if not args.no_mutants:
        mutants = mutation_analysis(DEV_SEEDS + TEST_SEEDS)
        print()
        print("mutant (a misreading of one clause)            records that expose it")
        for label, count in mutants.items():
            print(f"  {label:45} {count}")
            if count == 0:
                survivors.append(label)
        print(f"killed {len(mutants) - len(survivors)} / {len(mutants)} mutants")
    if args.json:
        Path(args.json).write_text(json.dumps({"agree": agree, "total": count,
                                               "by_goal": {g: [total[(g, True)], total[(g, True)] + total[(g, False)]] for g in goals},
                                               "mutants": mutants, "survivors": survivors,
                                               "disagreements": problems}, indent=2, default=str),
                                   encoding="utf-8")
    return 0 if not problems and not survivors else 1


if __name__ == "__main__":
    raise SystemExit(main())
