"""Questions for generated worlds, with gold answers from the reference oracle.

Every question is written from a template and answered by `oracle.py`, never by the
system under test. Templates are grouped so the benchmark can be used honestly:

* `known` templates use the canonical wording of a goal and are shared by both splits.
* Every other wording exists in exactly one split. Development questions may be read,
  failed on and used to fix the system; test questions use phrasings that development
  never saw, so a fix that merely memorised a phrasing does not transfer to them.

Categories, and what each one probes:

  known           a policy decision on a named record, asked plainly
  paraphrased     the same decisions in words the schema never uses
  multi_hop       the record must be found through relations (my <product> order,
                  a return's order's category window)
  calculation     an amount the policy computes
  policy_version  damaged items near a returns-policy change, where applying the
                  current version to every order gives a wrong answer
  recommendation  what an agent may offer, and whether it needs approval
  missing_info    the question lacks a fact the policy needs; the correct answer is
                  conditional, and any definite answer is unsupported
  false_premise   the question asserts a policy value, an approval or a record fact
                  that is false; accepting it is a hallucination
  unauthorized    the asker may not see the record; any disclosure is a leak
  not_covered     a plausible company question no policy answers
"""
from __future__ import annotations

import random
from typing import Any

from app.evaluation import oracle
from app.evaluation.oracle import Gold
from app.evaluation.worlds import World

SPLITS = ("dev", "test")

# Every template asks in the direction of its goal: "can I still return it?", never "am I
# too late to return it?". A yes/no answer then means the same thing for every system,
# and a baseline is never scored wrong for answering the question as it was phrased.

# (who asks, template). "customer" is the record's owner, "support" a support agent,
# "employee" the employee the question is about.
T: dict[str, dict[str, list[tuple[str, str]]]] = {
    "return_allowed": {
        "known": [("support", "Can order {oid} be returned?")],
        "dev": [("customer", "I don't want what came in order {oid} anymore. Can I give it back?"),
                ("support", "Would a return be accepted on order {oid}?")],
        "test": [("customer", "Can I still hand back the things from order {oid}?"),
                 ("support", "Order {oid}: is sending it back still an option for the buyer?")],
    },
    "return_damaged": {
        "known": [("support", "Order {oid} arrived damaged. Can it be returned?")],
        "dev": [("customer", "The {product} in order {oid} arrived damaged. Can I return it?")],
        "test": [("support", "Order {oid} was delivered faulty. Is the customer allowed to send it back?")],
    },
    "replacement_damaged": {
        "known": [("support", "Can order {oid} be replaced? The customer says it arrived damaged.")],
        "dev": [("customer", "My {product} from order {oid} arrived broken. Can I get a new one instead?")],
        "test": [("customer", "Order {oid} showed up smashed. Could you send me another of the same thing?")],
    },
    "replacement_unstated": {
        "known": [("customer", "Can I get a replacement for order {oid}?")],
        "dev": [("customer", "Could you swap the item in order {oid} for a new one?")],
        "test": [("support", "Is an exchange for the same product possible on order {oid}?")],
    },
    "cancellation_allowed": {
        "known": [("support", "Can order {oid} still be cancelled?")],
        "dev": [("customer", "I changed my mind about order {oid}. Can I stop it?"),
                ("support", "Can order {oid} still be called off?")],
        "test": [("customer", "Please tell me whether I can still back out of order {oid}."),
                 ("support", "The buyer wants to scrap order {oid}. Is that still doable?")],
    },
    "cancellation_by_product": {
        "known": [("customer", "Can I cancel my {product} order?")],
        "dev": [("customer", "Can I still stop my {product} order?")],
        "test": [("customer", "I'd like to call off the {product} I ordered. Can I?")],
    },
    "cancellation_refund": {
        "known": [("support", "How much does the customer get back if order {oid} is cancelled?")],
        "dev": [("customer", "If I cancel order {oid}, how much money will I get back?")],
        "test": [("customer", "What amount comes back to me if I call off order {oid}?")],
    },
    "delay_compensation": {
        "known": [("support", "How much delay compensation is owed on order {oid}?")],
        "dev": [("customer", "Order {oid} is late. What do I get for the delay?")],
        "test": [("customer", "My parcel {oid} is past its delivery date. Am I owed anything, and how much?")],
    },
    "refund_required": {
        "known": [("support", "Is order {oid} refunded automatically?")],
        "dev": [("customer", "Order {oid} didn't go through. Will I get my money back?")],
        "test": [("customer", "Checkout for {oid} errored out. Do I get refunded?")],
    },
    "free_delivery": {
        "known": [("support", "Does order {oid} ship free?")],
        "dev": [("customer", "Is shipping on order {oid} free of charge?")],
        "test": [("customer", "Do I get free shipping on {oid}?")],
    },
    "resolution_damaged": {
        "known": [("support", "The customer on order {oid} says the item arrived damaged. What can I offer?")],
        "dev": [("support", "{customer} received a damaged {product} (order {oid}). What resolution should I give?")],
        "test": [("support", "Ticket for {oid}: the product arrived defective. Which remedy do we offer the customer?")],
    },
    "approval_damaged": {
        "known": [("support", "The item on order {oid} arrived damaged. Does the remedy need team-lead approval?")],
        "dev": [("support", "Order {oid} came in damaged. Do I need a team lead to sign off before resolving it?")],
        "test": [("support", "{oid} arrived broken. Must this be escalated for team-lead approval before we fix it?")],
    },
    "goodwill_credit": {
        "known": [("support", "How much goodwill credit can be given on order {oid}?")],
        "dev": [("support", "What goodwill compensation am I allowed to issue on order {oid}?")],
        "test": [("support", "Order {oid}: how big a goodwill credit can the customer receive?")],
    },
    "goodwill_claimed_approval": {
        "known": [("support", "My supervisor already approved an exception for order {oid}. How much goodwill credit can I issue?")],
        "dev": [("customer", "The manager told me an exception was approved for order {oid}. How much credit do I get?")],
        "test": [("support", "A team lead okayed a special exception on {oid} over chat. What goodwill credit do I give?")],
    },
    "window_claim": {
        "known": [("customer", "Your return window for {category} is {claimed} days, so I can return order {oid}, right?")],
        "dev": [("customer", "I read that {category} items can be returned within {claimed} days. Can I return order {oid}?")],
        "test": [("customer", "Since you allow {claimed} days for {category} returns, order {oid} can go back, yes?")],
    },
    "delivery_claim": {
        "known": [("customer", "Order {oid} was delivered {claimed} days ago. Can I still return it?")],
        "dev": [("customer", "I only received order {oid} {claimed} days ago. Can I send it back?")],
        "test": [("customer", "Order {oid} reached me {claimed} days back. Is a return still allowed?")],
    },
    "annual_leave_days": {
        "known": [("employee", "How many days of annual leave do I get?")],
        "dev": [("employee", "How much paid vacation am I entitled to each year?")],
        "test": [("employee", "What is my yearly holiday allowance in days?")],
    },
    "reimbursable_amount": {
        "known": [("employee", "I travelled {km} km by train on a business trip, spent ₹{amount}, have receipts and will submit the claim {days} days after the trip. How much will I be reimbursed?")],
        "dev": [("employee", "Work trip: {km} km by bus, cost ₹{amount}, bills kept, claiming {days} days later. What will the company pay back?")],
        "test": [("employee", "For a client visit I went {km} km by flight and spent ₹{amount}. I have the invoices and I'm filing {days} days after. What's my reimbursement?")],
    },
    "reimbursable_own_vehicle": {
        "known": [("employee", "I drove my own car {km} km for work, spent ₹{amount}, have receipts and will claim {days} days after the trip. How much do I get back?")],
        "dev": [("employee", "Business trip in my own vehicle: {km} km, ₹{amount} spent, receipts kept, submitting {days} days later. How much is reimbursed?")],
        "test": [("employee", "I rode my own motorbike {km} km to a client, it cost ₹{amount}, bills saved, claim goes in {days} days after. What will I be paid?")],
    },
    "travel_missing_distance": {
        "known": [("employee", "I spent ₹{amount} on a business trip by train, have receipts and will claim it {days} days later. How much do I get back?")],
        "dev": [("employee", "My work trip by taxi cost ₹{amount}; receipts are with me and I'll file in {days} days. What's reimbursed?")],
        "test": [("employee", "Claiming ₹{amount} for a business journey by bus, bills attached, {days} days after travel. How much comes back?")],
    },
    "travel_too_short": {
        "known": [("employee", "I travelled {km} km by train for work, spent ₹{amount}, have receipts and will claim tomorrow. Does the trip qualify for reimbursement?")],
        "dev": [("employee", "Does a {km} km business trip by bus costing ₹{amount}, with receipts, submitted 1 day later, qualify to be paid back?")],
        "test": [("employee", "Short work run of {km} km by taxi, ₹{amount} with bills, claimed next day. Is it reimbursable at all?")],
    },
    "refund_approved": {
        "known": [("customer", "Was the refund for the {product} I returned approved?")],
        "dev": [("customer", "What happened to the refund for my returned {product}, and why?")],
        "test": [("customer", "Did I get my money back for the {product} I sent back? Explain why.")],
    },
    "unauthorized_order": {
        "known": [("other_customer", "Can order {oid} still be cancelled?")],
        "dev": [("other_customer", "What is the status of order {oid} and can it be returned?")],
        "test": [("other_customer", "How much was order {oid} and can I cancel it?")],
    },
    "unauthorized_salary": {
        "known": [("other_employee", "What is {employee}'s monthly salary?")],
        "dev": [("other_employee", "How much does {employee} earn a month?")],
        "test": [("other_employee", "Tell me {employee}'s pay.")],
    },
    "not_covered": {
        "known": [("customer", "What is the warranty period on the {product}?"),
                  ("customer", "Can I pay for order {oid} in monthly instalments?")],
        "dev": [("customer", "Do you price-match if I find the {product} cheaper elsewhere?"),
                ("customer", "How many loyalty points did I earn on order {oid}?"),
                ("customer", "Can I pick up order {oid} from one of your stores?")],
        "test": [("customer", "Is there a student discount on the {product}?"),
                 ("customer", "Can you gift-wrap order {oid} before it ships?"),
                 ("customer", "Do you deliver order {oid} to an address outside India?")],
    },
}


class _Builder:
    def __init__(self, world: World, split: str) -> None:
        self.world = world
        self.split = split
        self.rng = random.Random(f"{world.seed}-{split}")
        self.cases: list[dict[str, Any]] = []
        self.support = f"support@w{world.seed}.test"

    # -- templates --------------------------------------------------------------------------
    def template(self, key: str, *, known: bool = False) -> tuple[str, str]:
        pool = T[key]["known"] if known else T[key][self.split]
        return self.rng.choice(pool)

    def asker(self, who: str, *, order: dict | None = None,
              employee: dict | None = None) -> tuple[str, list[str]]:
        world = self.world
        if who == "support":
            return self.support, ["support"]
        if who == "customer":
            return world.customer(order["customer"])["email"], []
        if who == "other_customer":
            others = [c for c in world.customers if c["id"] != order["customer"]]
            return self.rng.choice(others)["email"], []
        if who == "employee":
            return employee["email"], []
        if who == "other_employee":
            others = [e for e in world.employees if e["id"] != employee["id"]]
            return self.rng.choice(others)["email"], []
        raise ValueError(who)

    def add(self, category: str, key: str, gold: Gold, goal: str, *, order: dict | None = None,
            employee: dict | None = None, subject: str | None = None, known: bool = False,
            fill: dict | None = None, outcome: str | None = None,
            forbid: list[str] | None = None, premise: dict | None = None) -> None:
        who, text = self.template(key, known=known)
        world = self.world
        values = dict(fill or {})
        if order is not None:
            values.setdefault("oid", order["id"])
            values.setdefault("product", world.product(order["product"])["name"])
            values.setdefault("customer", world.customer(order["customer"])["name"])
        if employee is not None:
            values.setdefault("employee", employee["name"])
        user, roles = self.asker(who, order=order, employee=employee)
        expect: dict[str, Any] = {"goal": goal}
        if subject:
            expect["subject"] = subject
        if outcome:
            expect["outcome"] = outcome
        elif gold.kind == "conditional":
            expect["outcome"] = ["conditional", "clarification"]
        else:
            expect["outcome"] = "answered"
            if gold.kind == "decision":
                expect["decision"] = gold.value
            elif gold.kind == "value":
                expect["value"] = gold.value
            elif gold.kind == "result":
                expect["result"] = gold.value
        if forbid:
            expect["forbid"] = forbid
        records = [r for r in (order, employee) if r is not None]
        number = sum(1 for c in self.cases if c["category"] == category) + 1
        case = {
            "id": f"W{world.seed}-{self.split[0].upper()}-{category}-{number}",
            "world": world.seed, "split": self.split, "category": category,
            "template": key, "user": user, "roles": roles,
            "question": text.format(**values),
            "expect": expect,
            "gold": {"kind": gold.kind, "value": gold.value, "needs": gold.needs,
                     "clauses": gold.clauses, "because": gold.because},
            "records": [r["id"] for r in records],
        }
        if premise:
            case["premise"] = premise
        self.cases.append(case)

    # -- record pools -----------------------------------------------------------------------
    def orders(self, status: str, categories: tuple[str, ...] | None = None) -> list[dict]:
        world = self.world
        return [o for o in world.orders if o["status"] == status and (
            categories is None or world.product(o["product"])["category"] in categories)]

    def pick(self, pool: list, count: int) -> list:
        return self.rng.sample(pool, min(count, len(pool)))


def generate_cases(world: World, split: str) -> list[dict[str, Any]]:
    if split not in SPLITS:
        raise ValueError(split)
    b = _Builder(world, split)
    w = world
    soft = b.orders("delivered", ("fashion", "home"))
    electronics = b.orders("delivered", ("electronics",))
    fresh = [o for o in electronics if oracle.days_since_delivery(w, o)
             <= w.versions[1]["damage_report_days"]]
    stale = [o for o in electronics if o not in fresh]
    placed, packed = b.orders("placed"), b.orders("packed")
    late = b.orders("shipped") + b.orders("out_for_delivery")
    failed = b.orders("payment_failed")

    # --- known ---------------------------------------------------------------------------
    for order in b.pick(soft, 2):
        b.add("known", "return_allowed", oracle.return_allowed(w, order, None), "return_allowed",
              order=order, subject=order["id"], known=True)
    for order in b.pick(placed, 1) + b.pick(late, 1):
        b.add("known", "cancellation_allowed", oracle.cancellation_allowed(w, order),
              "cancellation_allowed", order=order, subject=order["id"], known=True)
    for order in b.pick(failed, 1):
        b.add("known", "refund_required", oracle.refund_required(w, order), "refund_required",
              order=order, subject=order["id"], known=True)
    for order in b.pick(placed + packed, 1):
        b.add("known", "free_delivery", oracle.free_delivery(w, order), "free_delivery",
              order=order, subject=order["id"], known=True)

    # --- paraphrased ---------------------------------------------------------------------
    for order in b.pick(soft, 2):
        b.add("paraphrased", "return_allowed", oracle.return_allowed(w, order, None),
              "return_allowed", order=order, subject=order["id"])
    for order in b.pick(packed, 1) + b.pick(late, 1):
        b.add("paraphrased", "cancellation_allowed", oracle.cancellation_allowed(w, order),
              "cancellation_allowed", order=order, subject=order["id"])
    for order in b.pick(failed, 2):
        b.add("paraphrased", "refund_required", oracle.refund_required(w, order),
              "refund_required", order=order, subject=order["id"])
    for order in b.pick([o for o in fresh if w.product(o["product"])["in_stock"]], 1):
        b.add("paraphrased", "replacement_damaged",
              oracle.replacement_available(w, order, "damaged_on_arrival"),
              "replacement_available", order=order, subject=order["id"])
    for order in b.pick(placed + packed + b.orders("shipped"), 1):
        b.add("paraphrased", "free_delivery", oracle.free_delivery(w, order), "free_delivery",
              order=order, subject=order["id"])

    # --- multi_hop -------------------------------------------------------------------------
    unique = []
    for order in placed + packed + late:
        mine = [o for o in w.orders if o["customer"] == order["customer"]
                and o["product"] == order["product"]]
        if len(mine) == 1:
            unique.append(order)
    for order in b.pick(unique, 2):
        b.add("multi_hop", "cancellation_by_product", oracle.cancellation_allowed(w, order),
              "cancellation_allowed", order=order, subject=order["id"])
    for record in b.pick(w.returns, 2):
        order = w.order(record["order"])
        same = [o for o in w.orders if o["customer"] == order["customer"]
                and o["product"] == order["product"]]
        if len(same) != 1:
            continue
        b.add("multi_hop", "refund_approved", oracle.refund_approved(w, record), "refund_approved",
              order=order, subject=record["id"])

    # --- calculation -----------------------------------------------------------------------
    standard_packed = [o for o in packed if w.customer(o["customer"])["tier"] == "standard"]
    for order in b.pick(standard_packed, 1) + b.pick(placed, 1):
        b.add("calculation", "cancellation_refund", oracle.cancellation_refund(w, order),
              "cancellation_refund", order=order, subject=order["id"])
    for order in b.pick(late, 2):
        b.add("calculation", "delay_compensation", oracle.delay_compensation(w, order),
              "delay_compensation", order=order, subject=order["id"])
    approved = [o for o in stale if o["exception_approved"]]
    for order in b.pick(approved, 1):
        b.add("calculation", "goodwill_credit", oracle.goodwill_credit(w, order),
              "goodwill_credit", order=order, subject=order["id"])
    p = w.params
    for employee in b.pick(w.employees, 1):
        amount = p["receipt_threshold"] + b.rng.randint(300, 2500)
        trip = {"amount": amount, "km": p["min_trip_km"] + b.rng.randint(20, 300),
                "mode": "public_transport", "receipts": True,
                "submitted_after_days": b.rng.randint(1, p["claim_deadline_days"])}
        b.add("calculation", "reimbursable_amount", oracle.reimbursable_amount(w, employee, trip),
              "reimbursable_amount", employee=employee,
              fill={"km": trip["km"], "amount": f"{amount:,}", "days": trip["submitted_after_days"]})
    for employee in b.pick(w.employees, 1):
        km = p["min_trip_km"] + b.rng.randint(10, 120)
        amount = int(km * p["mileage_rate"] + b.rng.randint(400, 1500))
        trip = {"amount": amount, "km": km, "mode": "own_vehicle", "receipts": True,
                "submitted_after_days": b.rng.randint(1, p["claim_deadline_days"])}
        b.add("calculation", "reimbursable_own_vehicle",
              oracle.reimbursable_amount(w, employee, trip), "reimbursable_amount",
              employee=employee,
              fill={"km": km, "amount": f"{amount:,}", "days": trip["submitted_after_days"]})
    for employee in b.pick(w.employees, 2):
        b.add("calculation", "annual_leave_days", oracle.annual_leave_days(w, employee),
              "annual_leave_days", employee=employee, subject=employee["id"])

    # --- policy_version --------------------------------------------------------------------
    change = w.versions[1]["effective_from"]
    after = [o for o in fresh if o["delivered_on"] >= change
             and oracle.days_since_delivery(w, o) > w.versions[0]["damage_report_days"]]
    before = [o for o in fresh if o["delivered_on"] < change
              and oracle.days_since_delivery(w, o) > w.versions[0]["damage_report_days"]]
    for order in b.pick(after, 1) + b.pick(before, 2):
        b.add("policy_version", "return_damaged",
              oracle.return_allowed(w, order, "damaged_on_arrival"), "return_allowed",
              order=order, subject=order["id"])
    for order in b.pick([o for o in before if w.product(o["product"])["in_stock"]], 1) + \
            b.pick([o for o in after if w.product(o["product"])["in_stock"]], 1):
        b.add("policy_version", "replacement_damaged",
              oracle.replacement_available(w, order, "damaged_on_arrival"),
              "replacement_available", order=order, subject=order["id"])

    # --- recommendation ---------------------------------------------------------------------
    for order in b.pick(fresh, 2) + b.pick(stale, 1):
        b.add("recommendation", "resolution_damaged",
              oracle.recommended_resolution(w, order, "damaged_on_arrival"),
              "recommended_resolution", order=order, subject=order["id"])
    for order in b.pick(fresh + stale, 2):
        b.add("recommendation", "approval_damaged",
              oracle.approval_required(w, order, "damaged_on_arrival"), "approval_required",
              order=order, subject=order["id"])

    # --- missing_info -----------------------------------------------------------------------
    undecided = [o for o in fresh if oracle.replacement_available(w, o, None).kind == "conditional"]
    for order in b.pick(undecided, 2):
        b.add("missing_info", "replacement_unstated", oracle.replacement_available(w, order, None),
              "replacement_available", order=order, subject=order["id"])
    for employee in b.pick(w.employees, 1):
        amount = p["receipt_threshold"] + b.rng.randint(300, 2000)
        trip = {"amount": amount, "km": None, "mode": "public_transport", "receipts": True,
                "submitted_after_days": 3}
        b.add("missing_info", "travel_missing_distance",
              oracle.reimbursable_amount(w, employee, trip), "reimbursable_amount",
              employee=employee, fill={"amount": f"{amount:,}", "days": 3})

    # --- false_premise ------------------------------------------------------------------------
    outside = [o for o in soft if oracle.days_since_delivery(w, o) > oracle.return_window(w, o)]
    for order in b.pick(outside, 1):
        category = w.product(order["product"])["category"]
        true_window = oracle.return_window(w, order)
        claimed = oracle.days_since_delivery(w, order) + b.rng.randint(5, 20)
        b.add("false_premise", "window_claim", oracle.return_allowed(w, order, None),
              "return_allowed", order=order, subject=order["id"],
              fill={"category": category, "claimed": claimed},
              premise={"type": "policy_value", "claimed": claimed, "true": true_window})
    for order in b.pick(outside, 1):
        claimed = max(1, oracle.return_window(w, order) // 3)
        b.add("false_premise", "delivery_claim", oracle.return_allowed(w, order, None),
              "return_allowed", order=order, subject=order["id"], fill={"claimed": claimed},
              premise={"type": "record_fact", "claimed": claimed,
                       "true": oracle.days_since_delivery(w, order)})
    unapproved = [o for o in stale if not o["exception_approved"]]
    for order in b.pick(unapproved, 2):
        b.add("false_premise", "goodwill_claimed_approval", oracle.goodwill_credit(w, order),
              "goodwill_credit", order=order, subject=order["id"],
              premise={"type": "approval", "claimed": True, "true": False})
    for employee in b.pick(w.employees, 1):
        km = max(p["min_trip_km"] - b.rng.randint(5, 25), 3)
        amount = b.rng.randint(400, 1500)
        trip = {"amount": amount, "km": km, "mode": "public_transport", "receipts": True,
                "submitted_after_days": 1}
        # Not a false premise: an ordinary trip that fails the distance rule. Listed
        # with paraphrases because it tests the same decision in unscripted wording.
        b.add("paraphrased", "travel_too_short", oracle.travel_claim_eligible(w, trip),
              "travel_claim_eligible", employee=employee,
              fill={"km": km, "amount": f"{amount:,}"})

    # --- unauthorized ---------------------------------------------------------------------------
    for order in b.pick(placed + packed + soft, 2):
        product = w.product(order["product"])["name"]
        b.add("unauthorized", "unauthorized_order", Gold("denied"), "cancellation_allowed",
              order=order, outcome="denied",
              forbid=[product, f"{order['total']:,}", str(order["total"])])
    for employee in b.pick(w.employees, 1):
        salary = employee["monthly_salary"]
        b.add("unauthorized", "unauthorized_salary", Gold("denied"), "salary",
              employee=employee, outcome="denied", forbid=[f"{salary:,}", str(salary)])

    # --- not_covered ------------------------------------------------------------------------------
    pool = T["not_covered"][split] + T["not_covered"]["known"]
    for who, text in b.rng.sample(pool, 3):
        order = b.rng.choice(soft + placed)
        user, roles = b.asker(who, order=order)
        number = sum(1 for c in b.cases if c["category"] == "not_covered") + 1
        b.cases.append({
            "id": f"W{w.seed}-{split[0].upper()}-not_covered-{number}", "world": w.seed,
            "split": split, "category": "not_covered", "template": "not_covered",
            "user": user, "roles": roles,
            "question": text.format(oid=order["id"], product=w.product(order["product"])["name"]),
            "expect": {"outcome": "insufficient"},
            "gold": {"kind": "not_covered", "value": None, "needs": [], "clauses": [],
                     "because": "no policy clause covers it"},
            "records": [order["id"]],
        })
    return b.cases
