"""
Deterministic check of the cross-document selection fix. No model calls.

For each operation, builds the project bundle under the previous and the
revised Stage 1, then checks whether specific evidence passages reach it:
  keep   - passages behind correct values that the model should see
  drop   - passages behind known false positives that should not be retrieved
Passages are the quotes recorded in the audit logs. Matching is on a
normalized 60-character prefix of the quote.

Usage: python3 check_bundles.py OUT_JSONL
"""
import json, re, sys, importlib.util, unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CORPUS = REPO.parent / "corpus"
sys.path.insert(0, str(REPO / "src"))
import parse_loan_proposal as new  # noqa: E402
spec = importlib.util.spec_from_file_location("prev", HERE / "_prev_parse.py")
prev = importlib.util.module_from_spec(spec); spec.loader.exec_module(prev)

# (op, field, kind, quote source) -- quotes from the original run and the 20-op test
CASES = [
    ("AR-L1436", "price_escalation_clause", "keep", "new"),
    ("SU-L1021", "price_escalation_clause", "keep", "new"),
    ("EC-L1045", "imported_inputs_present", "keep", "new"),
    ("UR-L1070", "imported_inputs_present", "keep", "new"),
    ("BA-L1012", "fx_denomination", "keep", "orig"),
    ("BA-L1012", "safeguards_category", "keep", "orig"),
    ("EC-L1121", "procurement_modality", "keep", "orig"),
    ("BL-L1020", "construction_share_pct", "keep", "orig"),
    ("DR-L1080", "construction_share_pct", "keep", "orig"),
    ("UR-L1032", "construction_share_pct", "keep", "orig"),
    ("BA-L1012", "imported_inputs_present", "drop", "new"),
    ("CO-L1222", "imported_inputs_present", "drop", "new"),
    ("BL-L1020", "imported_inputs_present", "drop", "new"),
]

def norm(t):
    t = unicodedata.normalize("NFKD", t or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()

def quote(src, op, field):
    f = REPO / ("data/audit/microdata_audit.json" if src == "orig"
                else "evaluation/extraction_test/audit_new.json")
    for x in json.load(open(f))[op]["fields"]:
        if x["field"] == field:
            return x["quote"]

def docs(op):
    return [str(p) for p in sorted((CORPUS / op).iterdir())
            if p.is_file() and p.suffix.lower() in new.SUPPORTED_EXTENSIONS]

if __name__ == "__main__":
    out = Path(sys.argv[1])
    done = set()
    if out.exists():
        done = {json.loads(l)["op"] for l in out.read_text().splitlines() if l.strip()}
    for op in dict.fromkeys(c[0] for c in CASES):
        if op in done:
            continue
        d = docs(op)
        b_prev = norm(prev.build_project_bundle(d)[0])
        b_new = norm(new.build_project_bundle(d)[0])
        rows = []
        for cop, field, kind, src in CASES:
            if cop != op:
                continue
            q = norm(quote(src, op, field))[:60]
            rows.append({"field": field, "kind": kind, "quote60": q,
                         "in_prev": q in b_prev, "in_new": q in b_new})
        with out.open("a") as fh:
            fh.write(json.dumps({"op": op, "chars_prev": len(b_prev),
                                 "chars_new": len(b_new), "checks": rows}) + "\n")
        print(op, "done", flush=True)
