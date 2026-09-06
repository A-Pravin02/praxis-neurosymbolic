"""Build a human-phrased evaluation suite over machine-derived ground truth.

The generated suite (`build_dataset_suite.py`) has unimpeachable expected values
and five sentence templates. That tests attribute lookup well and phrasing
robustness not at all, which is the fairest criticism of the benchmark.

This suite fixes exactly one variable. Every case here reuses a generated case's
`expected_values`, `gold_documents` and `expected_kind` **by id**, and replaces
only the question text with a sentence a person would actually type. So a score
difference between the two suites is attributable to phrasing and nothing else:
the answers were still computed by arithmetic over the source rows, and no human
- including the one writing the questions - chose them.

The rewrites deliberately vary the things templates hold constant:

  * statistic words       mean / average / typical / "on average" / "what do X run"
  * superlatives          maximum / highest / peak / most / largest / ceiling
  * the word "dataset"    present, replaced by "table"/"data"/"file", or absent
  * sentence shape        question, imperative, possessive, embedded clause
  * domain vocabulary     "cheapest diamond" rather than "minimum price"

Usage:
    python scripts/build_curated_suite.py
    python scripts/run_eval.py curated --suite-file eval_suites/curated_suite.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATED = ROOT / "eval_suites" / "dataset_suite.json"
OUTPUT = ROOT / "eval_suites" / "curated_suite.json"

# (generated case id, human phrasing).
#
# Nothing but the sentence changes. If an id is missing from the generated suite
# the build fails loudly rather than silently shrinking the suite - a quietly
# shorter benchmark is worse than none.
REWRITES: list[tuple[str, str]] = [
    # --- row counts, without the template's "How many rows does X have?" -----
    ("rows-walmart",
     "How big is the walmart table?"),
    ("rows-diamonds",
     "How many diamonds are in the diamonds data?"),
    ("rows-kc_house_data",
     "I'm looking at kc_house_data - how many property records is that?"),
    ("rows-hr_data_test",
     "What's the row count on the test dataset?"),
    ("rows-solar_plant_plant_1_generation_data",
     "How many readings did Plant_1_Generation_Data record?"),
    ("rows-employee_promotion",
     "Tell me the size of the employee_promotion table."),
    ("rows-sales_db_train",
     "Roughly how many entries are in TRAIN?"),
    ("rows-voltas_power",
     "The Power dataset - how many observations does it hold?"),
    ("rows-worldwide_crop_consumption",
     "Number of rows in worldwide_crop_consumption?"),
    ("rows-voltas_currentvoltage",
     "How many samples does CurrentVoltage contain?"),
    ("rows-sales_db_test_final",
     "What is TEST_FINAL's total record count?"),
    ("rows-voltas_overview",
     "How many rows of data are there in Overview?"),

    # --- maxima, with superlatives the templates never use ------------------
    ("max-walmart-fuel_price",
     "What's the highest fuel price recorded in walmart?"),
    ("max-walmart-cpi",
     "In the walmart data, how high does CPI go?"),
    ("max-solar_plant_plant_1_generation_data-dc_power",
     "What was peak DC power at Plant_1_Generation_Data?"),
    ("max-solar_plant_plant_2_generation_data-dc_power",
     "Give me the largest DC POWER value in Plant_2_Generation_Data."),
    ("max-voltas_currentvoltage-il2",
     "What does IL2 top out at in CurrentVoltage?"),
    ("max-voltas_totalpower-kwh_i",
     "Maximum KWH I in the TotalPower table?"),
    ("max-vgsales-other_sales",
     "What's the most Other Sales any title in vgsales achieved?"),
    ("max-voltas_power-rval2",
     "The Power file - what's the ceiling on RVAL2?"),
    ("max-sales_db_test_final-store_id",
     "What is the highest Store id appearing in TEST_FINAL?"),
    ("max-voltas_powerfactor-thdvl1",
     "How large does THDVL1 get in PowerFactor?"),
    ("max-kc_house_data-waterfront",
     "What's the maximum value of waterfront in kc_house_data?"),
    ("max-voltas_powerfactor-pfl2",
     "PFL2 in PowerFactor - what's its upper bound?"),

    # --- minima, including domain vocabulary --------------------------------
    ("min-diamonds-price",
     "What's the cheapest diamond in the diamonds data?"),
    ("min-hr_data_test-age",
     "How young is the youngest person in the test dataset?"),
    ("min-employee_promotion-avg_training_score",
     "What's the lowest average training score in employee_promotion?"),
    ("min-wa_fn_usec_telco_customer_churn-monthlycharges",
     "What is the smallest monthly charge in WA_Fn-UseC_-Telco-Customer-Churn?"),
    ("min-gdp-gdp",
     "In the GDP data, what's the floor on gdp?"),
    ("min-voltas_power-rval3",
     "How low does RVAL3 go in Power?"),
    ("min-solar_plant_plant_1_generation_data-daily_yield",
     "Minimum DAILY YIELD at Plant_1_Generation_Data?"),
    ("min-diamonds-y",
     "What's the smallest y measurement among the diamonds?"),
    ("min-zomato-country code",
     "The zomato table - what's the lowest Country Code in it?"),
    ("min-worldwide_crop_consumption-value",
     "What's the bottom of the Value range in worldwide_crop_consumption?"),
    ("min-employee_promotion-is_promoted",
     "What is the minimum is promoted value in employee_promotion?"),
    ("min-solar_plant_plant_2_generation_data-ac_power",
     "Lowest AC POWER reading in Plant_2_Generation_Data?"),

    # --- means, avoiding the word "mean" wherever a person would ------------
    ("mean-telco_customer_churn-monthlycharges",
     "On the telco customer churn data - what do customers pay per month on average?"),
    ("mean-wa_fn_usec_telco_customer_churn-tenure",
     "What's the average tenure in WA_Fn-UseC_-Telco-Customer-Churn?"),
    ("mean-kc_house_data-zipcode",
     "What's the typical zipcode in kc_house_data?"),
    ("mean-hr_data_train-no_of_trainings",
     "How many trainings does someone in the train dataset take on average?"),
    ("mean-bank-day",
     "In the bank data, what's the average day?"),
    ("mean-gdp-year",
     "What year does the GDP dataset average out to?"),
    ("mean-zomato-longitude",
     "Average Longitude across the zomato table?"),
    ("mean-voltas_overview-wti",
     "What does WTI average in Overview?"),
    ("mean-sales_db_train-store_id",
     "What's the mean Store id across TRAIN?"),
    ("mean-bank-previous",
     "The bank file - what do the previous values average?"),
    ("mean-voltas_totalpower-mkvad",
     "Give me the average MKVAD in TotalPower."),
    ("mean-worldwide_crop_consumption-time",
     "What's the average TIME in worldwide_crop_consumption?"),
    ("mean-hr_data_test-employee_id",
     "What is the average employee id in the test dataset?"),
    ("mean-sales_db_test_final-holiday",
     "In TEST_FINAL, what does Holiday come out to on average?"),

    # --- refusals: attributes the named table does not hold -----------------
    ("absent-walmart",
     "What's the median household income in the walmart data?"),
    ("absent-diamonds",
     "What's the average carbon footprint of a diamond in the diamonds table?"),
    ("absent-hr_data_train",
     "How does the train dataset score on net promoter?"),
    ("absent-kc_house_data",
     "In kc_house_data, what's the typical median household income?"),
    ("absent-sales_db_train",
     "What's employee satisfaction like in TRAIN?"),
    ("absent-employee_promotion",
     "Tell me the average median household income in employee_promotion."),
    ("absent-hr_data_test",
     "How satisfied are the employees in the test dataset, on average?"),
    ("absent-solar_plant_plant_1_generation_data",
     "What is the mean median household income at Plant_1_Generation_Data?"),
    ("absent-voltas_currentvoltage",
     "What's the churn probability in CurrentVoltage?"),
    ("absent-sales_db_test_final",
     "Average customer lifetime value in TEST_FINAL?"),
    ("absent-voltas_power",
     "What return rate does the Power table show?"),
    ("absent-worldwide_crop_consumption",
     "What's the churn probability in worldwide_crop_consumption?"),
    ("absent-voltas_overview",
     "How big is the carbon footprint in Overview, on average?"),
    ("absent-solar_plant_plant_2_generation_data",
     "What's the average churn probability at Plant_2_Generation_Data?"),

    # --- refusals: the column exists, but in a different table --------------
    # These are the ones plain retrieval answers fluently and wrongly, because
    # the words match a real passage somewhere in the corpus.
    ("cross-walmart",
     "What's the average holiday in walmart?"),
    ("cross-diamonds",
     "What's the typical store in the diamonds data?"),
    ("cross-kc_house_data",
     "In kc_house_data, what does store average out to?"),
    ("cross-hr_data_train",
     "Average store in the train dataset?"),
    ("cross-sales_db_train",
     "What's the mean store across TRAIN?"),
    ("cross-employee_promotion",
     "Tell me the average store in employee_promotion."),
    ("cross-solar_plant_plant_1_generation_data",
     "What store does Plant_1_Generation_Data average?"),
    ("cross-hr_data_test",
     "What's the average store in the test dataset?"),
    ("cross-voltas_overview",
     "The Overview table - what's its mean store?"),
    ("cross-worldwide_crop_consumption",
     "Average store in worldwide_crop_consumption?"),
]


def build(generated_path: Path, output_path: Path) -> list[dict]:
    cases = json.loads(generated_path.read_text(encoding="utf-8"))
    source = {case["id"]: case for case in cases}

    missing = [case_id for case_id, _ in REWRITES if case_id not in source]
    if missing:
        raise SystemExit(
            "these generated cases no longer exist, so their ground truth cannot "
            f"be reused: {', '.join(missing)}\n"
            "Re-run build_dataset_suite.py, then update REWRITES to match."
        )

    seen: set[str] = set()
    built: list[dict] = []
    for case_id, question in REWRITES:
        if case_id in seen:
            raise SystemExit(f"{case_id} is rewritten twice; each may appear once")
        seen.add(case_id)

        original = source[case_id]
        case = dict(original)
        case["id"] = f"curated-{case_id}"
        case["question"] = question
        case["tags"] = [*original.get("tags", []), "curated", "paraphrase"]
        case["notes"] = (
            f"Human phrasing of `{case_id}`. Ground truth unchanged: "
            f"{original.get('notes', 'computed from the source rows.')}"
        )
        built.append(case)
    return built


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", type=Path, default=GENERATED)
    parser.add_argument("--out", type=Path, default=OUTPUT)
    args = parser.parse_args()

    cases = build(args.generated, args.out)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(cases, indent=1) + "\n", encoding="utf-8")

    answerable = sum(1 for c in cases if c["expected_kind"] != "abstain")
    print(f"wrote {len(cases)} curated cases to {args.out}")
    print(f"  {answerable} answerable, {len(cases) - answerable} where refusal is correct")


if __name__ == "__main__":
    main()
