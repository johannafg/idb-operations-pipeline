"""
Compare Stage 1 retrieval before and after the keyword and ranking changes,
on a fixed sample of operations. Deterministic; makes no model calls.

For each operation it records paragraphs parsed, paragraphs tagged relevant
under each version, and a retrieval-recall check: of the paragraphs whose
text mentions price adjustment or imported inputs (found by a broad
reference pattern over the full document), how many reach the bundle that
the model actually sees.

Usage: python3 compare_retrieval.py SAMPLE_FILE OUT_JSONL
"""
import json, re, sys, importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CORPUS = REPO.parent / "corpus"
sys.path.insert(0, str(REPO / "src"))
import parse_loan_proposal as new  # noqa: E402

spec = importlib.util.spec_from_file_location("old", HERE / "_old_parse.py")
old = importlib.util.module_from_spec(spec); spec.loader.exec_module(old)

REF = {
    "price_adjustment": re.compile(
        r"redetermin|reajust|price (adjust|escalat|variation)|escalation clause|"
        r"ajuste de pre|variaci[oó]n de precio|f[oó]rmulas? (polin|param)", re.I),
    "imports": re.compile(
        r"\bimported\b|\bimports\b|import of|importaci[oó]n|importa[cç][aã]o|importad[oa]s?\b", re.I),
}

def bundle_for(mod, paras, per_doc=8000):
    tagged = mod.tag_sections(paras)
    text, n_rel = mod.build_retrieval_bundle(tagged, max_chars=per_doc, source_name="D")
    return text, n_rel

def run(op):
    folder = CORPUS / op
    docs = sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in new.SUPPORTED_EXTENSIONS)
    rec = {"op": op, "n_docs": len(docs), "parsed": 0,
           "rel_old": 0, "rel_new": 0, "chars_old": 0, "chars_new": 0}
    for k in REF:
        rec[f"{k}_paras"] = 0; rec[f"{k}_in_old"] = 0; rec[f"{k}_in_new"] = 0
    for d in docs:
        if d.stat().st_size / 1048576 > new.MAX_FILE_SIZE_MB:
            continue
        try:
            paras = new.extract_paragraphs_any(str(d))
        except Exception:
            continue
        rec["parsed"] += len(paras)
        b_old, r_old = bundle_for(old, paras)
        b_new, r_new = bundle_for(new, paras)
        rec["rel_old"] += r_old; rec["rel_new"] += r_new
        rec["chars_old"] += len(b_old); rec["chars_new"] += len(b_new)
        for k, rx in REF.items():
            for p in paras:
                if rx.search(p["text"]):
                    rec[f"{k}_paras"] += 1
                    marker = f"¶{p['para_id']}, p.{p['page']}]"
                    rec[f"{k}_in_old"] += marker in b_old
                    rec[f"{k}_in_new"] += marker in b_new
    return rec

if __name__ == "__main__":
    sample = [l.strip() for l in open(sys.argv[1]) if l.strip()]
    out = Path(sys.argv[2])
    done = set()
    if out.exists():
        done = {json.loads(l)["op"] for l in out.read_text().splitlines() if l.strip()}
    with out.open("a") as fh:
        for op in sample:
            if op in done: continue
            try:
                rec = run(op)
            except Exception as e:
                rec = {"op": op, "error": str(e)[:200]}
            fh.write(json.dumps(rec) + "\n"); fh.flush()
            print(op, "done", flush=True)
