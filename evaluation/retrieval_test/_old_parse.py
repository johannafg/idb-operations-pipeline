"""
Stage 1 of the extraction agent: deterministic PDF/DOCX -> paragraph-indexed text.

IDB Loan Proposals use numbered paragraphs (1.1, 1.2, ... 2.4, 3.13, ...) and
lettered/roman section headers (I., II., A., B., ...). We exploit that fixed
structure to (a) split the document into addressable, citable units and
(b) locate the sections most likely to contain each schema field, so the
LLM extraction call in Stage 2 only ever sees relevant excerpts rather than
the full ~40-page document.

Many of the annexes the harvester downloads under the "Loan Proposal"
category (economic analysis, financial-instrument justification, gender
note, institutional strengthening plan, etc. -- see harvest_projects.py)
come as .docx rather than .pdf. extract_paragraphs_docx applies the same
numbered-paragraph convention where the document has one, and falls back to
one entry per Word paragraph where it doesn't -- mirroring the PDF path's
per-page fallback for unnumbered pages. Legacy binary .doc files are not
supported (python-docx only reads .docx); those are skipped with a note
rather than silently dropped.

This stage has no model calls and is fully deterministic/auditable.
"""
import json
import re
import sys
import time
from pathlib import Path

import pdfplumber

SUPPORTED_EXTENSIONS = {".pdf", ".docx"}

# pdfplumber/pdfminer can use memory wildly out of proportion to file size on
# image- or map-heavy scanned PDFs (a single 130 MB engineering-study PDF
# was enough to get this whole script OOM-killed, repeatedly, on a
# memory-constrained laptop). These are rarely where the schema's fields
# actually live (loan proposals and economic/IGAS annexes are almost always
# well under this), so skipping the outliers is a cheap trade for not
# crashing the entire run on one file.
MAX_FILE_SIZE_MB = 40


def _ensure_downloaded(path: str, attempts: int = 4, base_delay: float = 1.5) -> None:
    """Cloud-sync clients (OneDrive, etc.) can leave a file as an on-demand
    placeholder that isn't actually on disk yet; reading it then raises
    OSError [Errno 35] "Resource deadlock avoided" on macOS. That failure is
    usually transient -- retrying a moment later lets the sync client catch
    up -- so this touches the file first and retries before any real parser
    ever sees it, rather than letting a sync hiccup masquerade as '0
    paragraphs found in this document' the way it did previously."""
    last_exc = None
    for attempt in range(attempts):
        try:
            with open(path, "rb") as f:
                f.read(1)
            return
        except OSError as e:
            last_exc = e
            time.sleep(base_delay * (attempt + 1))
    raise OSError(
        f"could not read {path!r} after {attempts} attempts (last error: {last_exc}). "
        f"If this file lives in a cloud-synced folder (OneDrive, etc.), it may still be "
        f"an on-demand placeholder -- try opening it once in Finder, or set the folder to "
        f"'Always keep on this device', then re-run."
    ) from last_exc
PARA_RE = re.compile(r"^(\d{1,2}\.\d{1,2})\s+(.*)")
SECTION_KEYWORDS = {
    "financing": ["financing instruments", "cost and financing", "financing structure",
                  "public and private financing", "parallel financing", "cofinancing"],
    "procurement": ["procurement", "competitive bidding", "threshold amounts"],
    "risks": ["other risks", "fiduciary risks", "environmental and social risks",
              "medium risk", "high risk", "risk classification", "mitigation action",
              "main risks"],
    "execution": ["execution mechanism", "implementation arrangements", "executing agency"],
    "disbursement": ["disbursements", "disbursement schedule", "projected disbursements"],
    "safeguards": ["environmental and social considerations", "safeguard"],
}


def extract_paragraphs(pdf_path: str):
    """Return a list of {para_id, page, text} for every numbered paragraph,
    plus a fallback 'page_blob' for pages with no numbered paragraphs
    (cover page, tables, annexes)."""
    _ensure_downloaded(pdf_path)
    paragraphs = []
    current = None
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            page_has_para = False
            for line in text.split("\n"):
                m = PARA_RE.match(line.strip())
                if m:
                    page_has_para = True
                    if current:
                        paragraphs.append(current)
                    current = {"para_id": m.group(1), "page": page_num, "text": m.group(2)}
                elif current is not None:
                    current["text"] += " " + line.strip()
            if not page_has_para and text.strip():
                # cover page / table / annex page with no ¶ numbering
                paragraphs.append({"para_id": f"page-{page_num}", "page": page_num,
                                    "text": text.strip()[:4000]})
        if current:
            paragraphs.append(current)
    return paragraphs


def extract_paragraphs_docx(docx_path: str):
    """Same shape as extract_paragraphs (para_id, page, text), for .docx
    annexes. Word has no page-number concept at the text-extraction level
    (that's a rendering-time layout detail), so page is set to 'n/a' -- the
    paragraph id (when the document uses IDB's numbering) or a synthetic
    docx-pN id remains the real citation anchor either way."""
    _ensure_downloaded(docx_path)
    from docx import Document as DocxDocument
    doc = DocxDocument(docx_path)
    paragraphs = []
    current = None
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        m = PARA_RE.match(text)
        if m:
            if current:
                paragraphs.append(current)
            current = {"para_id": m.group(1), "page": "n/a", "text": m.group(2)}
        elif current is not None:
            current["text"] += " " + text
        else:
            # No numbered-paragraph convention found yet (common -- many
            # annexes are free-form prose, not written like the main
            # proposal). Fall back to one entry per Word paragraph.
            paragraphs.append({"para_id": f"docx-p{len(paragraphs) + 1}", "page": "n/a",
                                "text": text[:4000]})
    if current:
        paragraphs.append(current)
    return paragraphs


def extract_paragraphs_any(doc_path: str):
    """Dispatches to the right extractor by extension. Raises ValueError for
    anything not in SUPPORTED_EXTENSIONS (e.g. .xlsx, .ppt, legacy .doc) so
    callers can catch it and log a clear skip reason instead of a confusing
    parser crash."""
    ext = Path(doc_path).suffix.lower()
    if ext == ".pdf":
        return extract_paragraphs(doc_path)
    if ext == ".docx":
        return extract_paragraphs_docx(doc_path)
    raise ValueError(f"unsupported document type '{ext}' -- only {sorted(SUPPORTED_EXTENSIONS)} "
                      f"are parsed for text; this file's content will not be included")


def tag_sections(paragraphs):
    """Cheap keyword match to bucket paragraphs into candidate sections.
    A paragraph can match more than one bucket; unmatched paragraphs are
    still kept (e.g. background/rationale) but deprioritized."""
    tagged = []
    for p in paragraphs:
        low = p["text"].lower()
        hits = [tag for tag, kws in SECTION_KEYWORDS.items() if any(k in low for k in kws)]
        tagged.append({**p, "tags": hits})
    return tagged


def build_retrieval_bundle(tagged_paragraphs, max_chars=12000, source_name=None):
    """Stage-1 output: paragraphs matching ANY schema-relevant tag, concatenated
    with citations, capped so Stage 2's prompt stays small and cheap.

    source_name, if given, is prefixed to every citation so that when multiple
    documents are combined (loan proposal + annexes) each fact can still be
    traced back to the specific file it came from."""
    relevant = [p for p in tagged_paragraphs if p["tags"]]
    bundle, used = [], 0
    for p in relevant:
        cite = f"{source_name} ¶{p['para_id']}" if source_name else f"¶{p['para_id']}"
        chunk = f"[{cite}, p.{p['page']}] {p['text']}"
        if used + len(chunk) > max_chars:
            break
        bundle.append(chunk)
        used += len(chunk)
    return "\n\n".join(bundle), len(relevant)


def build_project_bundle(doc_paths, max_chars_per_doc=8000, max_chars_total=24000):
    """Run Stage 1 across every document belonging to one project (loan
    proposal + annexes -- PDF and DOCX alike) and concatenate their retrieval
    bundles, budget-capped per document so one long annex can't crowd out
    the others. Files of an unsupported type (e.g. .xlsx procurement plans,
    .ppt slides also filed under the same 'Loan Proposal' category) are
    skipped with a note in the bundle rather than silently dropped, so a
    reviewer scanning the bundle sees exactly what wasn't read. Returns
    (combined_text, total_paras, total_relevant, failed_documents) -- the
    last one is a list of {file, error} for anything that raised while
    parsing (as opposed to being an intentionally-unsupported type), so a
    caller can tell "genuinely no relevant paragraphs" apart from "this
    document was never actually read."""
    parts, total_paras, total_relevant, skipped, failed = [], 0, 0, [], []
    for doc_path in doc_paths:
        name = Path(doc_path).name
        try:
            size_mb = Path(doc_path).stat().st_size / (1024 * 1024)
        except OSError:
            size_mb = 0
        if size_mb > MAX_FILE_SIZE_MB:
            skipped.append(name)
            parts.append(f"[{name}] -- skipped, {size_mb:.0f} MB exceeds the "
                          f"{MAX_FILE_SIZE_MB} MB parsing limit (see MAX_FILE_SIZE_MB in "
                          f"parse_loan_proposal.py)")
            print(f"[note] {name}: {size_mb:.0f} MB exceeds MAX_FILE_SIZE_MB "
                  f"({MAX_FILE_SIZE_MB}) -- skipping to avoid the OOM risk large scanned "
                  f"PDFs pose", file=sys.stderr)
            continue
        try:
            paras = extract_paragraphs_any(doc_path)
        except ValueError as e:
            skipped.append(name)
            parts.append(f"[{name}] -- skipped, not parsed for text: {e}")
            continue
        except Exception as e:
            failed.append({"file": name, "error": str(e)})
            parts.append(f"[{name}] -- could not parse: {e}")
            continue
        tagged = tag_sections(paras)
        chunk, n_rel = build_retrieval_bundle(tagged, max_chars=max_chars_per_doc, source_name=name)
        total_paras += len(paras)
        total_relevant += n_rel
        if chunk:
            parts.append(chunk)
    if skipped:
        print(f"[note] {len(skipped)} file(s) not parsed (unsupported type): {', '.join(skipped)}",
              file=sys.stderr)
    if failed:
        print(f"[warn] {len(failed)} file(s) FAILED to parse (see stats.failed_documents): "
              f"{', '.join(f['file'] for f in failed)}", file=sys.stderr)
    combined = "\n\n".join(parts)
    if len(combined) > max_chars_total:
        combined = combined[:max_chars_total]
    return combined, total_paras, total_relevant, failed


if __name__ == "__main__":
    doc_path = sys.argv[1] if len(sys.argv) > 1 else "CO-L1234 LP English (1).pdf"
    paras = extract_paragraphs_any(doc_path)
    tagged = tag_sections(paras)
    bundle, n_relevant = build_retrieval_bundle(tagged)

    out = {
        "source_document": Path(doc_path).name,
        "n_paragraphs_total": len(paras),
        "n_paragraphs_relevant": n_relevant,
        "retrieval_bundle_chars": len(bundle),
    }
    print(json.dumps(out, indent=2))

    Path("retrieval_bundle.txt").write_text(bundle, encoding="utf-8")
    Path("paragraphs_full.json").write_text(json.dumps(tagged, indent=2), encoding="utf-8")
    print("\nWrote retrieval_bundle.txt and paragraphs_full.json")
