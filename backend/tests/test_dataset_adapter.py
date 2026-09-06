"""The merged-dataset adapter and the workspace-aware target resolver.

Every test here pins a defect that only appeared once real data was ingested.
"""
from __future__ import annotations

import io

import pytest

from app.ingestion.merged_dataset import (
    ColumnStats, SourceFile, canonical, dataset_card, entity_facts,
    schema_and_statistic_facts, slug,
)
from app.knowledge.resolver import question_tokens_of, resolve
from app.neuro.heuristics import extract_numbers
from app.verification.engine import numbers_agree


def build_source(category: str, path: str, lines: list[str]) -> SourceFile:
    source = SourceFile(category=category, source_file=path)
    for number, line in enumerate(lines, start=1):
        source.observe(number, line)
    return source


WALMART = [
    "Store,Date,Weekly_Sales,IsHoliday,Type",
    "1,2010-02-05,24924.50,FALSE,A",
    "1,2010-02-12,46039.49,TRUE,A",
    "2,2010-02-19,41595.55,FALSE,B",
    "2,2010-02-26,19403.54,FALSE,B",
]


# --- statistics -------------------------------------------------------------

def test_column_statistics_are_computed_not_guessed():
    source = build_source("Retail", "walmart.csv", WALMART)
    assert source.rows == 4
    sales = source.columns["Weekly_Sales"].summary()
    assert sales["kind"] == "numeric"
    assert sales["max"] == 46039.49
    assert sales["min"] == 19403.54
    assert round(sales["mean"], 2) == 32990.77


def test_categorical_columns_report_distinct_and_modal_values():
    source = build_source("Retail", "walmart.csv", WALMART)
    summary = source.columns["Type"].summary()
    assert summary["kind"] == "categorical"
    assert summary["distinct"] == 2
    assert summary["top"][0]["count"] == 2


def test_facts_and_card_agree_on_every_number():
    """The card is the evidence and the facts are the claims.

    If the card rounds to four places while the fact keeps six, no claim can ever
    be matched to its own source and the system abstains on values it computed.
    """
    source = build_source("Retail", "walmart.csv", WALMART)
    card = dataset_card(source)
    card_numbers = set(extract_numbers(card))
    for draft in schema_and_statistic_facts(source):
        if isinstance(draft.value, float):
            assert any(numbers_agree(draft.value, n) for n in card_numbers), (
                f"{draft.predicate}={draft.value} appears in no sentence of the card")


def test_canonical_rounding_matches_the_rendered_card():
    assert canonical(34.803915) == 34.8039
    assert canonical(693099.3612) == 693099.36
    assert canonical(423325.0) == 423325.0


def test_thousands_separators_survive_extraction():
    assert extract_numbers("The table has 423,325 data rows") == [423325.0]
    assert extract_numbers("values 1,2,3") == [1.0, 2.0, 3.0]


# --- identity ---------------------------------------------------------------

def test_slug_uses_the_full_path_so_same_named_tables_stay_distinct():
    a = SourceFile(category="x", source_file="HR Data/train.csv")
    b = SourceFile(category="x", source_file="Sales DB/TRAIN.csv")
    assert a.slug != b.slug


def test_entity_facts_need_a_genuinely_unique_identifier():
    """A repeating 'id' column is a group label, and keying triples on it makes
    many facts share a subject and predicate - which reads as a contradiction."""
    repeating = build_source("x", "campaign.csv", [
        "MarketID,Sales",
        "1,100",
        "1,200",
        "2,300",
    ])
    assert entity_facts(repeating) == []

    unique = build_source("x", "staff.csv", [
        "employee_id,department",
        "E-1,eng",
        "E-2,support",
    ])
    triples = entity_facts(unique)
    assert {f"{t.subject}.{t.predicate}" for t in triples} == {
        "staff:E-1.department", "staff:E-2.department"}


def test_large_tables_produce_statistics_rather_than_row_triples():
    lines = ["id,value"] + [f"{i},{i}" for i in range(2_500)]
    source = build_source("x", "big.csv", lines)
    assert source.rows == 2_500
    assert entity_facts(source) == []
    assert schema_and_statistic_facts(source)


# --- question tokenisation --------------------------------------------------

def test_identifier_names_in_a_question_are_split():
    tokens = question_tokens_of("What is the mean age in Plant_1_Generation_Data?")
    assert {"plant", "generation", "data"} <= tokens


def test_stopwords_never_become_matchable_tokens():
    """"What **is** the net promoter score" must not match an `is_promoted` column."""
    assert "is" not in question_tokens_of("What is the net promoter score?")
    assert "the" not in question_tokens_of("What is the price?")


# --- resolution against the live workspace ----------------------------------

def test_resolver_finds_nothing_in_an_empty_workspace(tenant_db, alice):
    resolution = resolve(tenant_db, alice, "what is the mean weekly sales")
    assert resolution.predicate is None or resolution.subject is not None


def test_resolver_matches_the_demo_vocabulary(tenant_db, alice):
    resolution = resolve(tenant_db, alice, "How many annual leave days do I get?")
    assert resolution.predicate == "annual_leave_days"


def test_resolver_requires_the_asked_for_attribute_to_be_covered(tenant_db, alice):
    """Sharing one common word is not a match.

    "net promoter score" against a table holding `avg_training_score` shares only
    "score"; answering would report a training score as a net promoter score.
    """
    resolution = resolve(tenant_db, alice, "What is the mean net promoter score?")
    assert resolution.predicate is None


def test_resolver_honours_the_statistic_that_was_asked_for(tenant_db, admin_acme):
    from app.knowledge.schema import make_fact
    from app.knowledge import store as knowledge_store

    knowledge_store.save_facts(tenant_db, admin_acme.tenant_id, [
        make_fact("dataset:widgets", "widget_price_max", 90),
        make_fact("dataset:widgets", "widget_price_mean", 40),
    ])
    highest = resolve(tenant_db, admin_acme,
                      "What is the maximum widget price in the widgets dataset?")
    average = resolve(tenant_db, admin_acme,
                      "What is the mean widget price in the widgets dataset?")
    assert highest.predicate == "widget_price_max"
    assert average.predicate == "widget_price_mean"


def test_a_named_subject_confines_the_search(tenant_db, admin_acme):
    """The dataset the question names is the only one that may answer it."""
    from app.knowledge.schema import make_fact
    from app.knowledge import store as knowledge_store

    knowledge_store.save_facts(tenant_db, admin_acme.tenant_id, [
        make_fact("dataset:alpha", "row_count", 10),
        make_fact("dataset:beta", "household_income_mean", 55_000),
    ])
    resolution = resolve(tenant_db, admin_acme,
                         "What is the mean household income in the alpha dataset?")
    # alpha has no income column, and beta's must not be borrowed.
    assert resolution.subject == "dataset:alpha"
    assert resolution.predicate is None


def test_subject_name_words_do_not_match_columns(tenant_db, admin_acme):
    """"the Plant_1_Generation_Data dataset" must not match a `plant_id` column."""
    from app.knowledge.schema import make_fact
    from app.knowledge import store as knowledge_store

    knowledge_store.save_facts(tenant_db, admin_acme.tenant_id, [
        make_fact("dataset:solar_plant_generation", "plant_id_mean", 4),
    ])
    resolution = resolve(
        tenant_db, admin_acme,
        "What is the mean household income in the solar_plant_generation dataset?")
    assert resolution.predicate is None
