#!/usr/bin/env python3
"""
Pipeline Fixes Punch List, item 7 (part 2): scores a completed (or
partially completed) validation_sample.xlsx from sample_for_validation.py
against the hand-coded values a reviewer filled in, producing the per-field
and overall agreement rate the methodology notes' measurement-error
robustness check needs.

Comparison is type-aware where schema.py declares a type (boolean/number),
so "true"/"True"/"1" all match each other for a boolean field, and "5" and
"5.0" match for a numeric one -- a naive string-equality comparison would
undercount agreement on formatting differences that don't reflect a real
extraction error.

Usage:
    python3 score_validation.py --sample ../output/validation_sample.xlsx \
        --out ../output/validation_scores.csv
"""
import argparse
import sys
from pathlib import Path

import openpyxl
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from schema import FIELD_DEFS

FIELD_TYPES = {name: typ for name, typ, _ in FIELD_DEFS}


def normalize(val, field_type: str):
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.lower() in ("none", "nan", "null"):
        return None
    if "boolean" in field_type:
        if s.lower() in ("true", "1", "yes"):
            return True
        if s.lower() in ("false", "0", "no"):
            return False
        return s.lower()
    if "number" in field_type:
        try:
            return round(float(s.replace(",", "").replace("%", "")), 2)
        except ValueError:
            return s.lower()
    return s.lower()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", default="../output/validation_sample.xlsx")
    ap.add_argument("--out", default="../output/validation_scores.csv")
    args = ap.parse_args()

    if not Path(args.sample).exists():
        print(f"[error] {args.sample} not found", file=sys.stderr)
        sys.exit(1)

    wb = openpyxl.load_workbook(args.sample, read_only=True)
    ws = wb["Validation Sample"]
    rows = list(ws.iter_rows(values_only=True))
    header = rows[0]
    idx = {h: i for i, h in enumerate(header)}

    records = []
    n_unreviewed = 0
    for r in rows[1:]:
        hand_coded = r[idx["hand_coded_value"]]
        if hand_coded is None or str(hand_coded).strip() == "":
            n_unreviewed += 1
            continue
        field = r[idx["field"]]
        field_type = FIELD_TYPES.get(field, "")
        extracted_norm = normalize(r[idx["extracted_value"]], field_type)
        hand_norm = normalize(hand_coded, field_type)
        records.append({
            "operation_number": r[idx["operation_number"]], "field": field,
            "status": r[idx["status"]], "extracted_value": r[idx["extracted_value"]],
            "hand_coded_value": hand_coded, "agree": extracted_norm == hand_norm,
        })

    if not records:
        print(f"[info] No rows have hand_coded_value filled in yet ({n_unreviewed} pending). "
              f"Nothing to score -- fill in the yellow columns in {args.sample} and re-run.",
              file=sys.stderr)
        sys.exit(0)

    df = pd.DataFrame(records)
    by_field = df.groupby("field")["agree"].agg(["count", "sum"])
    by_field["agreement_rate"] = (by_field["sum"] / by_field["count"]).round(3)
    by_field = by_field.rename(columns={"count": "n_reviewed", "sum": "n_agree"})

    overall_rate = df["agree"].mean()
    print(f"Reviewed: {len(df)} row(s) ({n_unreviewed} still pending -- blank hand_coded_value, excluded)")
    print(f"Overall agreement rate: {overall_rate:.1%}\n")
    print("By field:")
    print(by_field.to_string())

    disagreements = df[~df["agree"]]
    if len(disagreements):
        print(f"\n{len(disagreements)} disagreement(s) -- worth a closer look:")
        print(disagreements[["operation_number", "field", "extracted_value", "hand_coded_value"]].to_string(index=False))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    by_field.to_csv(args.out)
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
