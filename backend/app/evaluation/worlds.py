"""Procedurally generated companies for evaluation.

A benchmark written by hand against one knowledge pack measures how well a system fits
that pack. This module generates many companies instead: each world has its own policy
parameters, its own policy-version history and its own records, and every world is
answered by the *same* rule files. A result that holds across worlds is a property of
the method, not of one dataset its author knew.

Three design choices matter for what the benchmark can show.

Counterfactual policy values. Return windows, fee rates, thresholds and caps are drawn
from ranges that deliberately include values no real retailer publishes (a 23-day
window, a 6% fee capped at 750). A language model that answers from what retail
policies usually say is then wrong in a way the scorer can see, which is exactly the
failure the research question is about.

Boundary records. Orders are not sampled uniformly. Each world places records just
inside, exactly on, and just outside every window and threshold, and on both sides of
the returns-policy version change, because those are the cases where a plausible answer
and the correct one differ.

Independence from the system under test. Nothing here imports the reasoner. Gold answers
come from `oracle.py`, a separate reference implementation written from the policy text.

Everything is seeded: `generate_world(seed)` returns the same world on every machine.
"""
from __future__ import annotations

import datetime as dt
import random
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PACK = BACKEND_ROOT / "data" / "meridian"
WORLDS_DIR = BACKEND_ROOT / "data" / "benchmark_worlds"

# Seeds are fixed and disjoint. Development worlds may be inspected and used to fix the
# system; test worlds are generated, frozen and scored, never examined for tuning.
DEV_SEEDS = (11, 12, 13)
TEST_SEEDS = (101, 102, 103, 104, 105)

COMPANY = "meridian"   # the rule files name the company node; its display name is data

FIRST_NAMES = ["Aarav", "Meera", "Kabir", "Isha", "Vikram", "Nisha", "Arjun", "Tara",
               "Rahul", "Divya", "Farhan", "Leela", "Nikhil", "Sana", "Karthik", "Pooja",
               "Imran", "Anjali", "Siddharth", "Zoya"]
LAST_NAMES = ["Kapoor", "Reddy", "Menon", "Shah", "Pillai", "Bose", "Gupta", "Khan",
              "Das", "Varma", "Joshi", "Rao", "Chatterjee", "Kulkarni", "Singh", "Thomas"]
CITIES = ["bengaluru", "mumbai", "delhi", "pune", "chennai", "hyderabad", "kolkata"]

# Product catalogue per category: (name, low price, high price). Names are distinct across
# categories so a question naming a product identifies its category.
CATALOGUE = {
    "electronics": [("Vertex 15 Laptop", 42000, 95000), ("Pulse Wireless Earbuds", 1800, 6500),
                    ("Orbit Smartwatch", 3500, 14000), ("Lumen Tablet", 18000, 36000)],
    "fashion": [("Stride Running Shoes", 1500, 5500), ("Monsoon Rain Jacket", 1200, 4200),
                ("Denim Weekend Jeans", 900, 3200)],
    "home": [("Brass Table Lamp", 800, 3600), ("Cotton Duvet Cover", 1100, 4500),
             ("Cast Iron Skillet", 700, 2600)],
    "grocery": [("Kashmiri Saffron 2g", 300, 900), ("Cold Brew Coffee Pack", 250, 800)],
    "gift_card": [("Store Gift Card", 500, 5000)],
}


@dataclass
class World:
    seed: int
    as_of: dt.date
    company_name: str
    params: dict[str, Any]
    categories: dict[str, dict[str, Any]]
    grades: dict[str, int]
    versions: list[dict[str, Any]]          # returns policy versions, oldest first
    products: list[dict[str, Any]] = field(default_factory=list)
    customers: list[dict[str, Any]] = field(default_factory=list)
    orders: list[dict[str, Any]] = field(default_factory=list)
    payments: list[dict[str, Any]] = field(default_factory=list)
    returns: list[dict[str, Any]] = field(default_factory=list)
    refunds: list[dict[str, Any]] = field(default_factory=list)
    employees: list[dict[str, Any]] = field(default_factory=list)
    delivery_fee: int = 49

    @property
    def name(self) -> str:
        return f"world-{self.seed}"

    def product(self, product_id: str) -> dict[str, Any]:
        return next(p for p in self.products if p["id"] == product_id)

    def customer(self, customer_id: str) -> dict[str, Any]:
        return next(c for c in self.customers if c["id"] == customer_id)

    def order(self, order_id: str) -> dict[str, Any]:
        return next(o for o in self.orders if o["id"] == order_id)

    def payment(self, payment_id: str) -> dict[str, Any]:
        return next(p for p in self.payments if p["id"] == payment_id)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def generate_world(seed: int) -> World:
    rng = random.Random(seed)
    as_of = dt.date(2026, 9, 14) + dt.timedelta(days=rng.randint(0, 120))
    params = {
        "cancellation_fee_rate": rng.choice([0.03, 0.04, 0.06, 0.07, 0.08]),
        "cancellation_fee_cap": rng.choice([250, 350, 450, 600, 750]),
        "free_delivery_threshold_plus": rng.choice([299, 349, 449, 599]),
        "free_delivery_threshold_standard": rng.choice([699, 849, 1199, 1499]),
        "delay_threshold_days": rng.choice([2, 4, 5, 6]),
        "plus_delay_credit": rng.choice([60, 75, 120, 150]),
        "senior_service_years": rng.choice([2, 4, 5]),
        "annual_leave_base": rng.choice([15, 16, 20, 21]),
        "min_trip_km": rng.choice([35, 40, 60, 75]),
        "receipt_threshold": rng.choice([1200, 1500, 2500, 3000]),
        "claim_deadline_days": rng.choice([21, 45, 60]),
        "mileage_rate": rng.choice([7, 8, 11, 12]),
        "approval_threshold": rng.choice([12000, 18000, 22000, 35000]),
        "goodwill_rate": rng.choice([0.05, 0.08, 0.12, 0.15]),
        "goodwill_cap": rng.choice([250, 400, 650, 750]),
    }
    params["annual_leave_senior"] = params["annual_leave_base"] + rng.choice([3, 5, 7])
    delivery_fee = rng.choice([39, 59, 69, 79])
    soft_window = rng.choice([14, 21, 23, 35, 45])
    categories = {
        "fashion": {"return_window_days": soft_window,
                    "plus_extension_days": rng.choice([7, 10, 12, 20])},
        "home": {"return_window_days": rng.choice([14, 21, 28, 40]),
                 "plus_extension_days": rng.choice([7, 10, 12, 20])},
        "electronics": {"return_window_days": rng.choice([5, 7, 12, 14]),
                        "plus_extension_days": 0},
        "grocery": {"return_window_days": 0, "plus_extension_days": 0, "non_returnable": True},
        "gift_card": {"return_window_days": 0, "plus_extension_days": 0,
                      "non_returnable": True, "excluded_from_damage_returns": True},
    }
    l1 = rng.choice([3500, 4000, 6000])
    grades = {"L1": l1, "L2": l1 + rng.choice([2500, 3000, 4500]),
              "L3": l1 * 3 + rng.choice([0, 1500, 3000])}
    v1_days = rng.choice([4, 5, 6, 8])
    v2_days = v1_days + rng.choice([4, 6, 9])
    # The change took effect recently enough that orders delivered on *both* sides of it
    # can still be inside version 2's reporting period and outside version 1's. Only
    # there do the two versions disagree, so only there is version resolution tested.
    change = as_of - dt.timedelta(days=rng.randint(v1_days + 2, v2_days - 1))
    versions = [
        {"id": "RETURNS-v1", "version": 1, "effective_from": change - dt.timedelta(days=240),
         "damage_report_days": v1_days},
        {"id": "RETURNS-v2", "version": 2, "effective_from": change,
         "damage_report_days": v2_days, "supersedes": "RETURNS-v1"},
    ]
    world = World(seed, as_of, "Meridian Marketplace", params, categories, grades, versions,
                  delivery_fee=delivery_fee)
    _products(world, rng)
    _customers(world, rng)
    _orders(world, rng)
    _employees(world, rng)
    return world


def _products(world: World, rng: random.Random) -> None:
    number = 100 + rng.randint(0, 800)
    for category, items in CATALOGUE.items():
        for name, low, high in items:
            number += rng.randint(1, 9)
            price = int(round(rng.randint(low, high), -1)) - 1
            world.products.append({
                "id": f"PRD-{number}", "name": name, "category": category, "price": price,
                "in_stock": rng.random() > 0.3, "same_day_eligible": rng.random() > 0.5,
            })
    # Every world needs both stock states among damage-prone electronics, or the
    # replacement-versus-refund decision is never exercised.
    electronics = [p for p in world.products if p["category"] == "electronics"]
    electronics[0]["in_stock"], electronics[1]["in_stock"] = True, False


def _customers(world: World, rng: random.Random) -> None:
    firsts = rng.sample(FIRST_NAMES, 6)
    lasts = rng.sample(LAST_NAMES, 6)
    base = rng.randint(2000, 7000)
    for index in range(6):
        world.customers.append({
            "id": f"CUST-{base + index}",
            "name": f"{firsts[index]} {lasts[index]}",
            "email": f"{firsts[index].lower()}.{lasts[index].lower()}@w{world.seed}.test",
            "tier": "plus" if index % 2 == 0 else "standard",
            "city": rng.choice(CITIES),
        })


def _orders(world: World, rng: random.Random) -> None:
    """Orders placed on decision boundaries rather than sampled at random."""
    as_of, params = world.as_of, world.params
    serial = rng.randint(40000, 89000)
    pay_serial = rng.randint(5000, 9000)
    used_products: dict[str, set[str]] = {c["id"]: set() for c in world.customers}

    def product_for(customer: dict, category: str | None = None, *, stock: bool | None = None,
                    min_price: int = 0) -> dict:
        pool = [p for p in world.products
                if (category is None or p["category"] == category)
                and (stock is None or p["in_stock"] is stock)
                and p["price"] >= min_price
                and p["id"] not in used_products[customer["id"]]]
        if not pool:  # fall back to reuse rather than fail; names then stop being unique
            pool = [p for p in world.products if category is None or p["category"] == category]
        chosen = rng.choice(pool)
        used_products[customer["id"]].add(chosen["id"])
        return chosen

    def add(customer: dict, product: dict, status: str, **extra: Any) -> dict:
        nonlocal serial, pay_serial
        serial += rng.randint(1, 37)
        pay_serial += 1
        total = product["price"]
        free = (customer["tier"] == "plus" and total > params["free_delivery_threshold_plus"]) \
            or total >= params["free_delivery_threshold_standard"]
        payment = {"id": f"PAY-{pay_serial}", "status": extra.pop("payment_status", "captured"),
                   "method": rng.choice(["card", "upi", "wallet"])}
        world.payments.append(payment)
        order = {"id": f"ORD-{serial}", "customer": customer["id"], "product": product["id"],
                 "payment": payment["id"], "status": status, "total": total,
                 "delivery_fee_paid": 0 if free else world.delivery_fee,
                 "carrier": rng.choice(["BlueDart", "Delhivery", "Ekart", "Shadowfax"]),
                 "exception_approved": False, **extra}
        world.orders.append(order)
        return order

    customers = world.customers
    change = world.versions[1]["effective_from"]
    v1, v2 = (v["damage_report_days"] for v in world.versions)

    for index, customer in enumerate(customers):
        plus = customer["tier"] == "plus"
        # Cancellation stages.
        add(customer, product_for(customer), "placed",
            placed_on=as_of - dt.timedelta(days=rng.randint(0, 1)),
            promised_delivery=as_of + dt.timedelta(days=rng.randint(3, 6)))
        add(customer, product_for(customer, rng.choice(["fashion", "home"])), "packed",
            placed_on=as_of - dt.timedelta(days=rng.randint(1, 2)),
            promised_delivery=as_of + dt.timedelta(days=rng.randint(2, 5)))
        # Shipped late: one exactly on the delay threshold, one past it.
        late = params["delay_threshold_days"] + (0 if index % 3 == 0 else rng.randint(1, 3))
        add(customer, product_for(customer), rng.choice(["shipped", "out_for_delivery"]),
            placed_on=as_of - dt.timedelta(days=late + 5),
            promised_delivery=as_of - dt.timedelta(days=late))

        # Change-of-mind window boundaries for a soft-goods category.
        category = "fashion" if index % 2 == 0 else "home"
        window = world.categories[category]["return_window_days"] + \
            (world.categories[category]["plus_extension_days"] if plus else 0)
        offset = [0, -2, 3][index % 3]          # on, inside, outside the window
        days = max(window + offset, 1)
        add(customer, product_for(customer, category), "delivered",
            placed_on=as_of - dt.timedelta(days=days + 4),
            promised_delivery=as_of - dt.timedelta(days=days),
            delivered_on=as_of - dt.timedelta(days=days))

        # Damaged electronics: delivered after the version change, inside v2's reporting
        # period but outside v1's, so applying the newest *or* oldest policy blindly to
        # every case gives a wrong answer on one of these two records.
        since_change = (as_of - change).days
        if index < 4:
            after = rng.randint(v1 + 1, since_change)
            add(customer, product_for(customer, "electronics", stock=index % 2 == 0),
                "delivered", placed_on=as_of - dt.timedelta(days=after + 4),
                promised_delivery=as_of - dt.timedelta(days=after),
                delivered_on=as_of - dt.timedelta(days=after))
        else:
            # Delivered before the change, and still within version 2's reporting period
            # but past version 1's: governed by version 1, so a damage claim is out of
            # time even though the policy in force today would allow it.
            before = rng.randint(since_change + 1, v2)
            add(customer, product_for(customer, "electronics", stock=True), "delivered",
                placed_on=as_of - dt.timedelta(days=before + 4),
                promised_delivery=as_of - dt.timedelta(days=before),
                delivered_on=as_of - dt.timedelta(days=before))

        # Groceries: non-returnable for change of mind, returnable when damaged in time.
        grocery_days = rng.randint(1, 3)
        add(customer, product_for(customer, "grocery"), "delivered",
            placed_on=as_of - dt.timedelta(days=grocery_days + 2),
            promised_delivery=as_of - dt.timedelta(days=grocery_days),
            delivered_on=as_of - dt.timedelta(days=grocery_days))

        # A failed order: money taken for some, not for others.
        add(customer, product_for(customer), "payment_failed",
            placed_on=as_of - dt.timedelta(days=1),
            payment_status="captured" if index % 2 == 0 else "failed")

        # A high-value delivered order out of every window, with and without a
        # recorded supervisor exception (goodwill credit), for approval and goodwill.
        stale = world.categories["electronics"]["return_window_days"] + rng.randint(25, 40)
        expensive = add(customer, product_for(customer, "electronics",
                                              min_price=params["approval_threshold"] // 3),
                        "delivered", placed_on=as_of - dt.timedelta(days=stale + 3),
                        promised_delivery=as_of - dt.timedelta(days=stale),
                        delivered_on=as_of - dt.timedelta(days=stale))
        expensive["exception_approved"] = index % 2 == 1

    # Returns with a recorded refund decision, consistent with the refund policy.
    delivered = [o for o in world.orders if o["status"] == "delivered"
                 and world.product(o["product"])["category"] in ("fashion", "home")]
    rtn_serial = rng.randint(1000, 9000)
    for order in delivered[:4]:
        rtn_serial += rng.randint(1, 9)
        customer = world.customer(order["customer"])
        category = world.product(order["product"])["category"]
        window = world.categories[category]["return_window_days"] + (
            world.categories[category]["plus_extension_days"] if customer["tier"] == "plus" else 0)
        age = (as_of - order["delivered_on"]).days
        started_after = min(age, rng.choice([max(window - 1, 0), window, window + 2]))
        passed = rng.random() > 0.3
        world.returns.append({
            "id": f"RTN-{rtn_serial}", "order": order["id"],
            "started_on": order["delivered_on"] + dt.timedelta(days=started_after),
            "inspection": "passed" if passed else "failed"})
        approved = started_after <= window and passed
        world.refunds.append({"id": f"RFD-{rtn_serial}", "return": f"RTN-{rtn_serial}",
                              "status": "approved" if approved else "rejected"})


def _employees(world: World, rng: random.Random) -> None:
    firsts = rng.sample([n for n in FIRST_NAMES], 4)
    lasts = rng.sample(LAST_NAMES, 4)
    base = rng.randint(3000, 8000)
    senior = world.params["senior_service_years"]
    # Service clearly below and clearly above the senior threshold. Exact anniversaries
    # are avoided: "completed years" and a 365-day year disagree by leap days there, and
    # a benchmark should not hinge on an ambiguity in the policy's own wording.
    years = [senior - 1 + 0.4, senior + 0.5, senior - 0.3, senior + 2.2]
    for index in range(4):
        joined = world.as_of - dt.timedelta(days=int(years[index] * 365.25))
        world.employees.append({
            "id": f"EMP-{base + index}", "name": f"{firsts[index]} {lasts[index]}",
            "email": f"{firsts[index].lower()}.{lasts[index].lower()}@staff{world.seed}.test",
            "grade": ["L1", "L2", "L3", "L2"][index], "joined_on": joined,
            "monthly_salary": rng.randint(6, 30) * 10000,
        })


# ---------------------------------------------------------------------------
# Writing a knowledge pack
# ---------------------------------------------------------------------------

def write_pack(world: World, directory: Path | None = None) -> Path:
    """Write the world as a knowledge pack the unmodified system can load.

    The rule files are copied byte for byte from the source pack. Only the knowledge
    graph and the policy text differ between worlds.
    """
    root = directory or WORLDS_DIR / world.name
    if root.exists():
        shutil.rmtree(root)
    (root / "rules").mkdir(parents=True)
    (root / "knowledge").mkdir()
    (root / "policies").mkdir()
    for rules in sorted((SOURCE_PACK / "rules").glob("*.rules")):
        shutil.copyfile(rules, root / "rules" / rules.name)
    (root / "knowledge" / "company.yaml").write_text(
        f"# Generated world, seed {world.seed}. Do not edit: regenerate instead.\n"
        + yaml.safe_dump(_graph(world), sort_keys=False, allow_unicode=True), encoding="utf-8")
    for name, text in render_policies(world).items():
        (root / "policies" / name).write_text(text, encoding="utf-8")
    return root


def _graph(world: World) -> dict[str, Any]:
    p = world.params
    entities: list[dict[str, Any]] = [
        {"id": COMPANY, "type": "company", "name": world.company_name, "attributes": dict(p)}]
    for version in world.versions:
        attributes = {"policy": "returns", "version": version["version"],
                      "effective_from": version["effective_from"],
                      "damage_report_days": version["damage_report_days"]}
        if version.get("supersedes"):
            attributes["supersedes"] = version["supersedes"]
        entities.append({"id": version["id"], "type": "policy_version",
                         "name": f"Returns policy version {version['version']}",
                         "attributes": attributes})
    for category, attributes in world.categories.items():
        entities.append({"id": category, "type": "category",
                         "name": category.replace("_", " ").capitalize(),
                         "attributes": dict(attributes)})
    for product in world.products:
        entities.append({"id": product["id"], "type": "product", "name": product["name"],
                         "attributes": {"product_name": product["name"],
                                        "product_category": product["category"],
                                        "price": product["price"],
                                        "in_stock": product["in_stock"],
                                        "same_day_eligible": product["same_day_eligible"]}})
    for customer in world.customers:
        entities.append({"id": customer["id"], "type": "customer", "name": customer["name"],
                         "attributes": {"membership_tier": customer["tier"],
                                        "city": customer["city"], "email": customer["email"]}})
    for payment in world.payments:
        entities.append({"id": payment["id"], "type": "payment",
                         "attributes": {"payment_status": payment["status"],
                                        "payment_method": payment["method"]}})
    for order in world.orders:
        product = world.product(order["product"])
        attributes = {"placed_by": order["customer"], "contains": order["product"],
                      "paid_with": order["payment"], "order_status": order["status"],
                      "placed_on": order["placed_on"], "carrier": order["carrier"],
                      "order_total": order["total"],
                      "delivery_fee_paid": order["delivery_fee_paid"]}
        for key, attribute in (("promised_delivery", "promised_delivery"),
                               ("delivered_on", "delivered_on")):
            if order.get(key):
                attributes[attribute] = order[key]
        if order["exception_approved"]:
            attributes["exception_approved"] = True
        entities.append({"id": order["id"], "type": "order",
                         "name": f"{product['name']} order", "attributes": attributes})
    for record in world.returns:
        order = world.order(record["order"])
        entities.append({"id": record["id"], "type": "return",
                         "name": f"Return of the {world.product(order['product'])['name']}",
                         "attributes": {"return_for": record["order"],
                                        "return_started_on": record["started_on"],
                                        "inspection_result": record["inspection"]}})
    for refund in world.refunds:
        entities.append({"id": refund["id"], "type": "refund",
                         "name": f"Refund for return {refund['return']}",
                         "attributes": {"refund_for_return": refund["return"],
                                        "refund_status": refund["status"]}})
    for grade, cap in world.grades.items():
        entities.append({"id": grade, "type": "grade", "name": grade,
                         "attributes": {"travel_cap": cap}})
    for employee in world.employees:
        entities.append({"id": employee["id"], "type": "employee", "name": employee["name"],
                         "attributes": {"grade": employee["grade"],
                                        "joined_on": employee["joined_on"],
                                        "email": employee["email"],
                                        "monthly_salary": employee["monthly_salary"]}})
    return {"as_of": world.as_of, "entities": entities}


def _money(value: float) -> str:
    return f"{value:,.0f}"


def _percent(rate: float) -> str:
    return f"{rate * 100:g}%"


def render_policies(world: World) -> dict[str, str]:
    """The policy documents, written from the world's parameters.

    The wording follows the source pack's documents clause for clause; only the numbers
    change. Every number an answer could need appears in exactly one current clause, and
    the superseded returns policy stays in the corpus as a real document index would keep
    it - which is what makes version confusion possible for a retrieval baseline.
    """
    p, c, g = world.params, world.categories, world.grades
    v1, v2 = world.versions
    name = world.company_name
    returns_body = """## RET-1 Return window

A delivered item may be returned within its category's return window, counted in
days from the delivery date. Fashion items have a {fw}-day window. Home items have a
{hw}-day window. Electronics have a {ew}-day window.

## RET-2 Non-returnable categories

Groceries and gift cards cannot be returned for change of mind, whatever the
number of days since delivery.

## RET-3 Plus membership extended window

Plus members receive {fx} additional days on the return window for fashion items and
{hx} additional days for home items. The extension does not apply to electronics.

## RET-4 Damaged, defective or wrong items

An item that arrived damaged, defective, or was not the item ordered may be
returned in any category except gift cards, provided the problem is reported
within {dmg} days of delivery. This applies even to groceries and to electronics
outside their normal window.

## RET-5 Replacement instead of refund

A replacement is offered only when the item is eligible for return because it
arrived damaged, defective or wrong, the problem was reported within the damage
reporting period, and the same product is currently in stock. When the product is
out of stock, the customer receives a full refund instead.

## RET-6 Condition of returned items

Items returned for change of mind must be unused and in their original
packaging. Returns that fail inspection are not refunded.
"""
    windows = dict(fw=c["fashion"]["return_window_days"], hw=c["home"]["return_window_days"],
                   ew=c["electronics"]["return_window_days"],
                   fx=c["fashion"]["plus_extension_days"], hx=c["home"]["plus_extension_days"])
    docs = {
        "returns_v1.md": (
            f"# Returns and Replacements Policy\n\n{name} (generated evaluation company).\n"
            f"Policy: returns. Version: 1. Effective: {v1['effective_from'].isoformat()}.\n"
            f"Superseded by version 2 on {v2['effective_from'].isoformat()}. Orders delivered "
            f"before that date remain governed\nby this version.\n\n"
            + returns_body.format(dmg=v1["damage_report_days"], **windows)),
        "returns.md": (
            f"# Returns and Replacements Policy\n\n{name} (generated evaluation company).\n"
            f"Policy: returns. Version: 2. Effective: {v2['effective_from'].isoformat()}. "
            f"Supersedes version 1.\nChange from version 1: damaged, defective or wrong items "
            f"may now be reported within\n{v2['damage_report_days']} days of delivery instead of "
            f"{v1['damage_report_days']}. It applies to orders delivered on or after "
            f"{v2['effective_from'].isoformat()}.\n\n"
            + returns_body.format(dmg=v2["damage_report_days"], **windows)),
        "orders_and_refunds.md": f"""# Orders, Cancellations and Refunds Policy

{name} (generated evaluation company).

## CAN-1 Free cancellation before packing

An order may be cancelled free of charge while its status is "placed".

## CAN-2 Cancellation after packing

An order whose status is "packed" may still be cancelled. Customers who are not
Plus members pay a cancellation fee of {_percent(p['cancellation_fee_rate'])} of the order total, capped at
{_money(p['cancellation_fee_cap'])} rupees. Plus members pay no cancellation fee.

## CAN-3 No cancellation after shipping

Once an order has shipped it can no longer be cancelled. The customer may refuse
the delivery, or return the item after delivery under the Returns Policy.

## REF-1 Refund on cancellation

When an order is cancelled, the refund is the order total minus any cancellation
fee.

## REF-2 Refund approval for returns

A refund for a returned item is approved when the return was started within the
item's return window (including any Plus extension) and the returned item passed
inspection.

## REF-3 Refund rejection

A refund for a returned item is rejected when the return was started after the
applicable window, or when the returned item failed inspection.

## REF-4 Payment deducted but order failed

If a payment was captured from the customer but the order failed to be created,
the full amount must be refunded automatically within 3 business days. No
request from the customer is needed.
""",
        "delivery.md": f"""# Delivery Policy

{name} (generated evaluation company).

## DEL-1 Same-day delivery

Same-day delivery is available for products marked same-day eligible.

## DEL-2 Free delivery

Plus members get free delivery on every order whose total exceeds {_money(p['free_delivery_threshold_plus'])}
rupees. Other customers get free delivery on orders of {_money(p['free_delivery_threshold_standard'])} rupees or more.
Otherwise a delivery fee of {world.delivery_fee} rupees applies.

## DEL-3 Delayed deliveries

An order is delayed when it has not been delivered by its promised delivery date.
If a delivery is more than {p['delay_threshold_days']} days late, the delivery fee is refunded. Plus
members additionally receive a {p['plus_delay_credit']}-rupee wallet credit.
""",
        "support_operations.md": f"""# Support Operations Playbook

{name} (generated evaluation company).
Policy: support. Version: 1. Effective: {(world.as_of - dt.timedelta(days=200)).isoformat()}.

## SUP-1 Order of resolutions

When a customer reports a problem with an order, the agent offers the first remedy the
customer is eligible for, in this order: a replacement of the same product under RET-5;
a full refund when a damaged, defective or wrong item is eligible for return but cannot
be replaced; cancellation of an order that has not shipped under CAN-1 and CAN-2; a
standard return, refunded after inspection, under RET-1. When none applies, there is no
standard remedy and the agent must not promise one.

## SUP-2 Approval for high-value remedies

A replacement, refund or return on an order whose total is above {_money(p['approval_threshold'])} rupees must be
approved by a team lead before it is issued.

## SUP-3 Goodwill exceptions

When no standard remedy applies, a goodwill credit may be issued only if a supervisor's
approval of the exception is recorded on the order. The credit is {_percent(p['goodwill_rate'])} of the order
total, capped at {_money(p['goodwill_cap'])} rupees. A customer or agent saying that approval was given is not a
recorded approval.

## SUP-4 Routing of support issues

Payment problems go to the Payments team; late, lost or undelivered packages go to the
Logistics desk; damaged, defective or wrong items go to the Returns desk; requests to
cancel or change an order go to Order care.
""",
        "employee_handbook.md": f"""# Employee Handbook: Leave, Travel and Expenses

{name} (generated evaluation company).

## LV-1 Annual leave

Employees receive {p['annual_leave_base']} days of annual leave per year. Employees with at least
{p['senior_service_years']} completed years of service receive {p['annual_leave_senior']} days.

## TRV-1 Business travel reimbursement caps

Business travel expenses are reimbursed up to a per-trip cap that depends on the
employee's grade: {_money(g['L1'])} rupees for grade L1, {_money(g['L2'])} rupees for grade L2, and
{_money(g['L3'])} rupees for grade L3. The reimbursement is the amount claimed or the cap,
whichever is lower.

## TRV-2 Minimum distance

A trip qualifies for travel reimbursement only if the distance travelled is at
least {p['min_trip_km']} km.

## TRV-3 Own-vehicle mileage

When an employee travels in their own vehicle, the reimbursement is further
limited to a mileage allowance of {p['mileage_rate']} rupees per km.

## TRV-4 Receipts

Receipts are required for any travel claim above {_money(p['receipt_threshold'])} rupees.

## TRV-5 Submission deadline

Travel claims must be submitted within {p['claim_deadline_days']} days of the trip.
""",
    }
    return docs


def sync_rules(root: Path) -> bool:
    """Refresh a world's rule files from the source pack.

    The rules are part of the system under test, not of the benchmark: a world freezes its
    records, its policy text and its questions, but it is always answered by the current
    rules. Returns True when a file changed.
    """
    changed = False
    for rules in sorted((SOURCE_PACK / "rules").glob("*.rules")):
        target = root / "rules" / rules.name
        if not target.exists() or target.read_bytes() != rules.read_bytes():
            shutil.copyfile(rules, target)
            changed = True
    return changed


def build_all(seeds: tuple[int, ...] = DEV_SEEDS + TEST_SEEDS) -> list[Path]:
    return [write_pack(generate_world(seed)) for seed in seeds]
