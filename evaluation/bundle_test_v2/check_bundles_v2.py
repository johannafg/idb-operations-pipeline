"""
Passage check after pinning the project summary page. No model calls.

Two groups of passages, taken from the audit logs:
  regained  - evidence behind correct values found in the 20-operation test
              (v1) but lost in the 11-operation re-test (v2)
  retained  - evidence behind values the re-test gained, which pinning must
              not crowd out
Usage: python3 check_bundles_v2.py OUT_JSONL
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
V1 = json.load(open(REPO / "evaluation/extraction_test/audit_new.json"))
V2 = json.load(open(REPO / "evaluation/extraction_test_v2/audit_new.json"))
CASES = [  # (op, field, group, source)
    ("AR-L1436", "cofinancing_share_pct", "regained", V1),
    ("SU-L1021", "construction_share_pct", "regained", V1),
    ("UR-L1070", "fx_denomination", "regained", V1),
    ("BL-L1020", "cofinancing_present", "regained", V1),
    ("BL-L1020", "counterpart_funding_share_pct", "regained", V1),
    ("DR-L1080", "cofinancing_present", "regained", V1),
    ("DR-L1031", "construction_share_pct", "regained", V1),
    ("BA-L1012", "fx_denomination", "retained", V2),
    ("BA-L1012", "safeguards_category", "retained", V2),
    ("EC-L1121", "procurement_modality", "retained", V2),
    ("DR-L1080", "construction_share_pct", "retained", V2),
    ("AR-L1436", "price_escalation_clause", "retained", V2),
    ("SU-L1021", "price_escalation_clause", "retained", V2),
]
def norm(t):
    t = unicodedata.normalize("NFKD", t or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()
def quote(src, op, field):
    return next(x["quote"] for x in src[op]["fields"] if x["field"] == field)
if __name__ == "__main__":
    out = Path(sys.argv[1]); done = set()
    if out.exists():
        done = {json.loads(l)["op"] for l in out.read_text().splitlines() if l.strip()}
    for op in dict.fromkeys(c[0] for c in CASES):
        if op in done: continue
        d = [str(p) for p in sorted((CORPUS / op).iterdir())
             if p.is_file() and p.suffix.lower() in new.SUPPORTED_EXTENSIONS]
        bp, bn = norm(prev.build_project_bundle(d)[0]), norm(new.build_project_bundle(d)[0])
        rows = []
        for cop, field, group, src in CASES:
            if cop != op: continue
            q = norm(quote(src, op, field))
            q = q.split(" ... ")[0][:50] if q else ""
            rows.append({"field": field, "group": group, "q": q, "in_prev": bool(q) and q in bp, "in_new": bool(q) and q in bn})
        with out.open("a") as fh:
            fh.write(json.dumps({"op": op, "checks": rows}) + "\n")
        print(op, "done", flush=True)
