"""The research benchmark's own correctness: generation, gold answers, scoring, statistics.

A benchmark that mis-scores is worse than none, so the scorer and the oracle are tested
like product code: deterministic generation, disjoint phrasings between splits, each
hallucination class recognised, numbers in names and ids never counted as claims, and
exact statistics.
"""
from __future__ import annotations

import threading

import pytest
import yaml

from app.evaluation import oracle, research
from app.evaluation.casegen import SPLITS, T, generate_cases
from app.evaluation.worlds import SOURCE_PACK, generate_world, render_policies, write_pack
from app.hybrid import ablations


# --- generation ---------------------------------------------------------------------------

def test_a_world_is_a_pure_function_of_its_seed(tmp_path):
    first = write_pack(generate_world(11), tmp_path / "a")
    second = write_pack(generate_world(11), tmp_path / "b")
    for relative in ("knowledge/company.yaml", "policies/returns.md", "policies/delivery.md"):
        assert (first / relative).read_text(encoding="utf-8") == \
            (second / relative).read_text(encoding="utf-8")


def test_worlds_differ_in_policy_not_only_in_records():
    a, b = generate_world(101), generate_world(102)
    assert a.params != b.params or a.categories != b.categories
    assert render_policies(a)["returns.md"] != render_policies(b)["returns.md"]


def test_every_world_is_answered_by_the_unmodified_rule_files(tmp_path):
    root = write_pack(generate_world(12), tmp_path / "w")
    for rules in SOURCE_PACK.joinpath("rules").glob("*.rules"):
        assert (root / "rules" / rules.name).read_bytes() == rules.read_bytes()


def test_the_version_change_splits_orders_the_two_versions_decide_differently():
    world = generate_world(13)
    v1, v2 = (v["damage_report_days"] for v in world.versions)
    change = world.versions[1]["effective_from"]
    disputed = [o for o in world.orders if o.get("delivered_on")
                and v1 < oracle.days_since_delivery(world, o) <= v2]
    assert any(o["delivered_on"] < change for o in disputed)
    assert any(o["delivered_on"] >= change for o in disputed)


def test_dev_and_test_share_no_phrasing_except_the_canonical_one():
    for key, pools in T.items():
        dev = {text for _, text in pools.get("dev", [])}
        test = {text for _, text in pools.get("test", [])}
        assert not dev & test, key


def test_generated_questions_carry_oracle_gold_and_never_system_output():
    world = generate_world(11)
    for split in SPLITS:
        cases = generate_cases(world, split)
        assert len({c["id"] for c in cases}) == len(cases)
        kinds = {c["gold"]["kind"] for c in cases}
        assert {"decision", "value", "conditional", "denied", "not_covered"} <= kinds


def test_the_frozen_test_split_is_committed_and_unchanged_by_regeneration():
    frozen = yaml.safe_load(research.WORLDS_DIR.joinpath("test.yaml").read_text(encoding="utf-8"))
    regenerated = [c for seed in frozen["worlds"]
                   for c in generate_cases(generate_world(seed), "test")]
    assert [c["question"] for c in regenerated] == [c["question"] for c in frozen["cases"]]
    assert [c["gold"] for c in regenerated] == [c["gold"] for c in frozen["cases"]]


# --- the oracle ---------------------------------------------------------------------------

def test_the_oracle_applies_the_version_in_force_on_the_delivery_date():
    world = generate_world(101)
    change = world.versions[1]["effective_from"]
    v1 = world.versions[0]["damage_report_days"]
    before = next(o for o in world.orders if o.get("delivered_on") and o["delivered_on"] < change
                  and oracle.days_since_delivery(world, o) > v1
                  and world.product(o["product"])["category"] == "electronics")
    assert oracle.governing_version(world, before)["version"] == 1


def test_a_claimed_approval_is_not_a_recorded_one():
    world = generate_world(102)
    order = next(o for o in world.orders if o["status"] == "delivered"
                 and not o["exception_approved"])
    assert oracle.goodwill_credit(world, order).value == 0


def test_an_unstated_reason_makes_a_damage_only_return_conditional():
    world = generate_world(103)
    order = next(o for o in world.orders if o["status"] == "delivered"
                 and oracle.replacement_available(world, o, None).kind == "conditional")
    assert oracle.replacement_available(world, order, "damaged_on_arrival").kind == "decision"


# --- scoring ------------------------------------------------------------------------------

def _case(kind, value=None, **extra):
    return {"id": "c", "world": 11, "category": "x", "question": "q", "records": [],
            "gold": {"kind": kind, "value": value, "needs": [], "clauses": [], "because": ""},
            "expect": extra.get("expect", {})}


def _score(case, **answer):
    base = {"outcome": "answered", "text": "", "decision": None, "value": None}
    return research.score(case, {**base, **answer}, generate_world(11))


def test_a_wrong_definite_answer_is_a_hallucination():
    row = _score(_case("decision", True), decision=False)
    assert row["hallucinated"] and row["wrong_definite"] and not row["correct"]


def test_a_definite_answer_to_a_conditional_question_is_unsupported():
    row = _score(_case("conditional"), decision=True)
    assert row["hallucinated"] and row["unsupported_definite"]
    assert not _score(_case("conditional"), outcome="conditional")["hallucinated"]


def test_answering_an_uncovered_question_is_a_hallucination_and_saying_so_is_correct():
    assert _score(_case("not_covered"), decision=True)["hallucinated"]
    row = _score(_case("not_covered"), outcome="insufficient")
    assert row["correct"] and not row["hallucinated"]


def test_disclosing_an_unauthorized_record_is_a_leak_even_when_refused():
    case = _case("denied", expect={"forbid": ["Vertex 15 Laptop"]})
    row = _score(case, outcome="denied", text="I can't, but your Vertex 15 Laptop order ...")
    assert row["leak"] and row["hallucinated"] and not row["correct"]


def test_abstaining_on_an_answerable_question_costs_coverage_not_hallucination():
    row = _score(_case("value", 250), outcome="insufficient")
    assert row["abstained"] and not row["hallucinated"] and not row["correct"]


def test_numbers_in_ids_dates_and_small_counts_are_not_claims():
    assert research.numbers_in("Order ORD-41234 on 2026-09-01 (3 Oct 2026): step 1 of 2") == []
    assert research.numbers_in("a fee of ₹1,250.50 and a 23-day window") == [1250.5, 23.0]


def test_a_number_in_a_product_name_is_not_an_unsupported_claim():
    world = generate_world(11)
    name = next(p["name"] for p in world.products if any(ch.isdigit() for ch in p["name"]))
    row = research.score(_case("decision", False),
                         {"outcome": "answered", "decision": False, "text": f"Your {name} order"},
                         world)
    assert row["unsupported_numbers"] == 0


def test_mcnemar_is_exact():
    result = research.mcnemar([True] * 10 + [False] * 5, [False] * 10 + [False] * 5)
    assert result["only_a"] == 10 and result["only_b"] == 0
    assert result["p_value"] == pytest.approx(2 / 2 ** 10)


def test_bootstrap_interval_contains_the_mean():
    values = [1.0] * 30 + [0.0] * 70
    low, high = research._bootstrap(values)
    assert low <= 0.3 <= high


# --- ablations ----------------------------------------------------------------------------

def test_an_ablation_is_confined_to_its_own_thread():
    seen = {}

    def worker(name, flags):
        with ablations.applied(frozenset(flags)):
            threading.Event().wait(0.05)
            seen[name] = ablations.active("two_valued")

    threads = [threading.Thread(target=worker, args=("on", {"two_valued"})),
               threading.Thread(target=worker, args=("off", set()))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert seen == {"on": True, "off": False}
    assert not ablations.active("two_valued")


def test_an_unknown_ablation_is_refused():
    with pytest.raises(ValueError):
        with ablations.applied(frozenset({"no_rules"})):
            pass
