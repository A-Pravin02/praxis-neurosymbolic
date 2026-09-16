"""Reference oracle: the gold answer for a generated question, computed from the policy text.

This module is the benchmark's ground truth, so it is written to be independent of the
system it scores. It does not import the rule language, the reasoner, the knowledge
graph or the knowledge pack. It reads a `World` - plain Python data - and implements each
policy clause directly, in the order the clause states it. Where the two implementations
disagree, one of them is wrong, and `scripts/check_oracle.py` reports every disagreement
so that it is adjudicated against the policy text rather than silently resolved in
favour of either side.

Three-valued answers. A question can lack a fact the policy needs ("my item arrived
broken" is not in any record). The oracle then returns `Gold(kind="conditional")` with
the facts that would decide it, and never guesses: a system that states a definite
answer to such a question has asserted something the knowledge does not support.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from app.evaluation.worlds import World

DAMAGE_REASONS = ("damaged_on_arrival", "wrong_item")


@dataclass
class Gold:
    kind: str                     # decision | value | result | conditional
    value: Any = None             # bool for decisions, number for values, text for results
    needs: list[str] = field(default_factory=list)   # facts that would decide a conditional
    because: str = ""             # the deciding clause, for adjudicating disagreements
    clauses: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Derived facts, each from one clause
# ---------------------------------------------------------------------------

def days_since_delivery(world: World, order: dict) -> int | None:
    delivered = order.get("delivered_on")
    return (world.as_of - delivered).days if delivered else None


def governing_version(world: World, order: dict) -> dict:
    """RET header: an order is governed by the version in force on its delivery date."""
    reference = order.get("delivered_on") or world.as_of
    in_force = [v for v in world.versions if v["effective_from"] <= reference]
    return max(in_force, key=lambda v: v["effective_from"]) if in_force else world.versions[0]


def return_window(world: World, order: dict) -> int:
    """RET-1 and RET-3: the category window plus the Plus extension where it applies."""
    category = world.product(order["product"])["category"]
    tier = world.customer(order["customer"])["tier"]
    window = world.categories[category]["return_window_days"]
    if tier == "plus":
        window += world.categories[category]["plus_extension_days"]
    return window


def _category(world: World, order: dict) -> dict:
    return world.categories[world.product(order["product"])["category"]]


def _change_of_mind_ok(world: World, order: dict) -> bool:
    """RET-1 and RET-2."""
    if order["status"] != "delivered" or _category(world, order).get("non_returnable"):
        return False
    return days_since_delivery(world, order) <= return_window(world, order)


def _damage_ok(world: World, order: dict) -> bool:
    """RET-4, under the governing version, assuming the item did arrive damaged."""
    if order["status"] != "delivered" or _category(world, order).get("excluded_from_damage_returns"):
        return False
    return days_since_delivery(world, order) <= governing_version(world, order)["damage_report_days"]


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

def return_allowed(world: World, order: dict, reason: str | None) -> Gold:
    if order["status"] != "delivered":
        return Gold("decision", False, because="not delivered (RET-1)", clauses=["RET-1"])
    if _change_of_mind_ok(world, order):
        return Gold("decision", True, because="within the return window (RET-1, RET-3)",
                    clauses=["RET-1", "RET-3"])
    if reason in DAMAGE_REASONS:
        return Gold("decision", _damage_ok(world, order), because="damage window (RET-4)",
                    clauses=["RET-4"])
    if reason is None and _damage_ok(world, order):
        return Gold("conditional", needs=["return_reason"],
                    because="only returnable if it arrived damaged or wrong (RET-4)",
                    clauses=["RET-4"])
    return Gold("decision", False, because="outside every window (RET-1, RET-2, RET-4)",
                clauses=["RET-1", "RET-2", "RET-4"])


def replacement_available(world: World, order: dict, reason: str | None) -> Gold:
    """RET-5."""
    eligible = _damage_ok(world, order)
    in_stock = world.product(order["product"])["in_stock"]
    if not eligible or (reason is not None and reason not in DAMAGE_REASONS):
        return Gold("decision", False, because="not eligible for a damage return (RET-5)",
                    clauses=["RET-5"])
    if not in_stock:
        return Gold("decision", False, because="out of stock, full refund instead (RET-5)",
                    clauses=["RET-5"])
    if reason is None:
        return Gold("conditional", needs=["return_reason"],
                    because="needs the item to have arrived damaged or wrong (RET-5)",
                    clauses=["RET-5"])
    return Gold("decision", True, because="damaged, in time, in stock (RET-5)", clauses=["RET-5"])


def cancellation_allowed(world: World, order: dict) -> Gold:
    status = order["status"]
    if status in ("placed", "packed"):
        return Gold("decision", True, because=f"status {status} (CAN-1, CAN-2)",
                    clauses=["CAN-1", "CAN-2"])
    return Gold("decision", False, because=f"status {status} (CAN-3)", clauses=["CAN-3"])


def cancellation_fee(world: World, order: dict) -> float:
    """CAN-1 and CAN-2."""
    if order["status"] == "placed" or world.customer(order["customer"])["tier"] == "plus":
        return 0.0
    p = world.params
    return min(order["total"] * p["cancellation_fee_rate"], p["cancellation_fee_cap"])


def cancellation_refund(world: World, order: dict) -> Gold:
    """REF-1."""
    return Gold("value", round(order["total"] - cancellation_fee(world, order), 2),
                because="order total minus the cancellation fee (REF-1, CAN-2)",
                clauses=["REF-1", "CAN-2"])


def refund_required(world: World, order: dict) -> Gold:
    """REF-4."""
    captured = world.payment(order["payment"])["status"] == "captured"
    ok = order["status"] == "payment_failed" and captured
    return Gold("decision", ok, because="payment captured on a failed order (REF-4)"
                if ok else "no money was captured (REF-4)", clauses=["REF-4"])


def delay_compensation(world: World, order: dict) -> Gold:
    """DEL-3."""
    p = world.params
    late = (world.as_of - order["promised_delivery"]).days
    if late <= p["delay_threshold_days"]:
        return Gold("value", 0, because=f"{late} days late, not more than "
                                        f"{p['delay_threshold_days']} (DEL-3)", clauses=["DEL-3"])
    amount = order["delivery_fee_paid"]
    if world.customer(order["customer"])["tier"] == "plus":
        amount += p["plus_delay_credit"]
    return Gold("value", amount, because=f"{late} days late (DEL-3)", clauses=["DEL-3"])


def free_delivery(world: World, order: dict) -> Gold:
    """DEL-2."""
    p = world.params
    plus = world.customer(order["customer"])["tier"] == "plus"
    ok = (plus and order["total"] > p["free_delivery_threshold_plus"]) or \
        order["total"] >= p["free_delivery_threshold_standard"]
    return Gold("decision", ok, because="free-delivery thresholds (DEL-2)", clauses=["DEL-2"])


def recommended_resolution(world: World, order: dict, reason: str | None) -> Gold:
    """SUP-1: the first remedy the customer is eligible for, in the playbook's order."""
    damaged = reason in DAMAGE_REASONS
    if damaged and _damage_ok(world, order) and world.product(order["product"])["in_stock"]:
        return Gold("result", "replacement", because="SUP-1 via RET-5", clauses=["SUP-1", "RET-5"])
    returnable = _change_of_mind_ok(world, order) or (damaged and _damage_ok(world, order))
    if returnable and damaged and not world.product(order["product"])["in_stock"]:
        return Gold("result", "full_refund", because="SUP-1: damaged, out of stock",
                    clauses=["SUP-1", "RET-5"])
    if order["status"] in ("placed", "packed"):
        return Gold("result", "cancel_order", because="SUP-1 via CAN-1/CAN-2",
                    clauses=["SUP-1", "CAN-1"])
    if returnable:
        return Gold("result", "return_and_refund_after_inspection", because="SUP-1 via RET-1",
                    clauses=["SUP-1", "RET-1"])
    if order["exception_approved"]:
        return Gold("result", "goodwill_credit", because="SUP-3 recorded approval",
                    clauses=["SUP-3"])
    return Gold("result", "no_standard_remedy", because="SUP-1: none applies", clauses=["SUP-1"])


def approval_required(world: World, order: dict, reason: str | None) -> Gold:
    """SUP-2."""
    remedy = recommended_resolution(world, order, reason).value
    ok = remedy in ("replacement", "full_refund", "return_and_refund_after_inspection") and \
        order["total"] > world.params["approval_threshold"]
    return Gold("decision", ok, because=f"remedy {remedy}, threshold (SUP-2)", clauses=["SUP-2"])


def goodwill_credit(world: World, order: dict) -> Gold:
    """SUP-3. A claimed approval is not a recorded one."""
    if not order["exception_approved"]:
        return Gold("value", 0, because="no recorded supervisor approval (SUP-3)",
                    clauses=["SUP-3"])
    p = world.params
    return Gold("value", round(min(order["total"] * p["goodwill_rate"], p["goodwill_cap"]), 2),
                because="rate of the total, capped (SUP-3)", clauses=["SUP-3"])


def service_years(world: World, employee: dict) -> float:
    return (world.as_of - employee["joined_on"]).days / 365


def annual_leave_days(world: World, employee: dict) -> Gold:
    """LV-1."""
    p = world.params
    senior = service_years(world, employee) >= p["senior_service_years"]
    return Gold("value", p["annual_leave_senior"] if senior else p["annual_leave_base"],
                because="LV-1", clauses=["LV-1"])


def senior_leave_eligible(world: World, employee: dict) -> Gold:
    ok = service_years(world, employee) >= world.params["senior_service_years"]
    return Gold("decision", ok, because="LV-1", clauses=["LV-1"])


def travel_claim_eligible(world: World, trip: dict) -> Gold:
    """TRV-2, TRV-4, TRV-5. `trip` holds what the employee stated; absent keys are unknown."""
    p = world.params
    needs = []
    km = trip.get("km")
    if km is not None and km < p["min_trip_km"]:
        return Gold("decision", False, because="below the minimum distance (TRV-2)",
                    clauses=["TRV-2"])
    if trip.get("submitted_after_days") is not None and \
            trip["submitted_after_days"] > p["claim_deadline_days"]:
        return Gold("decision", False, because="past the submission deadline (TRV-5)",
                    clauses=["TRV-5"])
    amount = trip.get("amount")
    if amount is not None and amount > p["receipt_threshold"] and trip.get("receipts") is False:
        return Gold("decision", False, because="no receipts above the threshold (TRV-4)",
                    clauses=["TRV-4"])
    if km is None:
        needs.append("travel_distance_km")
    if amount is not None and amount > p["receipt_threshold"] and trip.get("receipts") is None:
        needs.append("receipts_attached")
    if trip.get("submitted_after_days") is None:
        needs.append("claim_submitted_after_days")
    if needs:
        return Gold("conditional", needs=needs, because="TRV-2/TRV-4/TRV-5 need facts",
                    clauses=["TRV-2", "TRV-4", "TRV-5"])
    return Gold("decision", True, because="meets TRV-2, TRV-4, TRV-5",
                clauses=["TRV-2", "TRV-4", "TRV-5"])


def reimbursable_amount(world: World, employee: dict, trip: dict) -> Gold:
    """TRV-1 and TRV-3, for an eligible trip."""
    eligibility = travel_claim_eligible(world, trip)
    if eligibility.kind == "conditional":
        return Gold("conditional", needs=eligibility.needs, because=eligibility.because,
                    clauses=eligibility.clauses)
    if eligibility.value is False:
        return Gold("decision", False, because=eligibility.because, clauses=eligibility.clauses)
    cap = world.grades[employee["grade"]]
    amount = min(trip["amount"], cap)
    if trip.get("mode") == "own_vehicle":
        amount = min(amount, trip["km"] * world.params["mileage_rate"])
    return Gold("value", amount, because="lower of claim, cap (and mileage) (TRV-1, TRV-3)",
                clauses=["TRV-1", "TRV-3"])


def refund_approved(world: World, record: dict) -> Gold:
    """REF-2 and REF-3."""
    order = world.order(record["order"])
    started = (record["started_on"] - order["delivered_on"]).days
    ok = started <= return_window(world, order) and record["inspection"] == "passed"
    return Gold("decision", ok, because="REF-2/REF-3 window and inspection",
                clauses=["REF-2", "REF-3"])


# ---------------------------------------------------------------------------
# Numbers a correct answer may state
# ---------------------------------------------------------------------------

def supported_numbers(world: World, *records: dict, question: str = "") -> set[float]:
    """Every number the knowledge can back for a question about these records.

    Used for claim-level scoring: a number in an answer that is in none of these is a
    quantity the answer asserted without support - a window, fee, cap or amount that is
    not this company's. The set is generous by design (every policy parameter of the
    world, not only the relevant one), so the resulting unsupported-number rate is a lower
    bound on numeric hallucination, never an overcount.
    """
    import re

    p = world.params
    numbers: set[float] = {float(v) for v in p.values()}
    numbers |= {round(float(v) * 100, 4) for k, v in p.items() if k.endswith("_rate")}
    for category in world.categories.values():
        numbers |= {float(v) for v in category.values() if not isinstance(v, bool)}
        numbers.add(float(category["return_window_days"] + category["plus_extension_days"]))
    numbers |= {float(v["damage_report_days"]) for v in world.versions}
    numbers |= {float(v["version"]) for v in world.versions}
    numbers |= {float(c) for c in world.grades.values()}
    numbers |= {float(world.delivery_fee), 3.0}   # REF-4's three business days
    for record in records:
        for key, value in record.items():
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                numbers.add(float(value))
            elif isinstance(value, dt.date):
                numbers.add(float(abs((world.as_of - value).days)))
                numbers |= {float(value.day), float(value.year)}
        if "product" in record:
            product = world.product(record["product"])
            numbers.add(float(product["price"]))
        if record.get("delivered_on"):
            numbers.add(float(days_since_delivery(world, record)))
        if record.get("promised_delivery"):
            numbers.add(float((world.as_of - record["promised_delivery"]).days))
    numbers |= {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", question)}
    return numbers
