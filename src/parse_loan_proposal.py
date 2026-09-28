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
import unicodedata
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
# Retrieval keywords, matched against accent-stripped lowercase text, so
# "licitación" and "licitacion" both hit "licitacion". The corpus is mostly
# Spanish and Portuguese, so every group carries English, Spanish, and
# Portuguese terms. Terms are chosen to avoid false substrings: "imported",
# not "import", which would also match "important".
SECTION_KEYWORDS = {
    "financing": [
        "financing instruments", "cost and financing", "financing structure",
        "public and private financing", "parallel financing", "cofinancing",
        "co-financing", "counterpart", "currency", "loan currency", "united states dollars",
        "costo y financiamiento", "costos y financiamiento", "estructura de financiamiento",
        "cofinanciamiento", "cofinanciacion", "financiamiento paralelo",
        "aporte local", "contrapartida", "moneda",
        "custo e financiamento", "estrutura de financiamento", "cofinanciamento",
        "financiamento paralelo", "moeda",
    ],
    "procurement": [
        "procurement", "competitive bidding", "threshold amounts",
        "international competitive bidding", "national competitive bidding",
        "licitacion publica internacional", "licitacion publica nacional",
        "licitacao publica internacional", "licitacao publica nacional",
        "adquisiciones", "licitacion", "contrataciones", "contratacion directa",
        "aquisicoes", "licitacao", "contratacoes",
    ],
    "risks": [
        "other risks", "fiduciary risks", "environmental and social risks",
        "medium risk", "high risk", "risk classification", "mitigation action", "main risks",
        "riesgos de contexto", "riesgos fiduciarios", "riesgos especificos",
        "riesgos de capacidad", "principales riesgos", "mitigacion",
        "riscos fiduciarios", "principais riscos", "mitigacao",
    ],
    "execution": [
        "execution mechanism", "implementation arrangements", "executing agency",
        "mecanismo de ejecucion", "esquema de ejecucion", "organismo ejecutor",
        "unidad ejecutora", "agencia ejecutora",
        "mecanismo de execucao", "orgao executor", "unidade executora",
    ],
    "disbursement": [
        "disbursements", "disbursement schedule", "projected disbursements",
        "disbursement period", "periodo de desembolso", "plazo de desembolso",
        "desembolso",
    ],
    "safeguards": [
        "environmental and social considerations", "safeguard",
        "impact category", "environmental category", "classified as category",
        "category a", "category b", "category c",
        "clasificacion ambiental", "clasificado como categoria", "categoria a", "categoria b",
        "categoria c", "classificado como categoria",
        "salvaguardia", "salvaguarda", "ambiental y social", "socioambiental",
        "ambiental e social",
    ],
    "works": [
        "civil works", "construction works", "investment in infrastructure",
        "infrastructure investment", "inversion en infraestructura", "investimento em infraestrutura",
        "obras civiles", "obras de infraestructura", "construccion de",
        "obras civis", "construcao de",
    ],
    "execution_conditions": [
        "retroactive financing", "advance contracting", "advance procurement",
        "conditions precedent", "special conditions of execution",
        "financiamiento retroactivo", "contratacion anticipada", "adquisiciones anticipadas",
        "condiciones especiales de ejecucion", "condiciones contractuales especiales",
        "financiamento retroativo", "contratacao antecipada", "condicoes especiais de execucao",
    ],
    "cost_table": [
        "cost and financing", "project cost", "goods and services", "equipment and materials",
        "costo y financiamiento", "costo del proyecto", "adquisicion de bienes",
        "bienes y servicios", "equipamiento", "presupuesto por componente",
        "custo e financiamento", "aquisicao de bens", "bens e servicos",
        "projected disbursements", "disbursement schedule", "cronograma de desembolsos",
        "plan financiero", "cronograma de inversiones", "cronograma de desembolso",
    ],
    "eligibility": [
        "eligibility", "eligible for disbursement", "eligibility date",
        "conditions prior to the first disbursement", "first disbursement",
        "elegibilidad", "fecha de elegibilidad", "elegible para desembolso",
        "condiciones previas al primer desembolso", "primer desembolso",
        "elegibilidade", "data de elegibilidade", "primeiro desembolso",
    ],
    "price_adjustment": [
        "price adjustment", "price escalation", "price variation", "escalation clause",
        "price redetermination",
        "redeterminacion", "reajuste", "ajuste de precios", "variacion de precios",
        "formula polinomica", "formulas polinomicas", "formulas parametricas",
        "reajuste de precos", "ajuste de precos",
    ],
}

# Groups serving fields that appear in only a few paragraphs of a document.
# They are weighted up so that, when a document has more relevant text than
# the character budget allows, these paragraphs are kept first.
TAG_WEIGHTS = {"price_adjustment": 3, "eligibility": 3, "execution_conditions": 3,
               "cost_table": 2, "works": 2}


def _norm(text: str) -> str:
    """Lowercase and strip diacritics, so matching is accent-insensitive."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


_NORM_KEYWORDS = {tag: [_norm(k) for k in kws] for tag, kws in SECTION_KEYWORDS.items()}


def _pages_via_pdfplumber(pdf_path: str):
    with pdfplumber.open(pdf_path) as pdf:
        return [(page.extract_text() or "") for page in pdf.pages]


def _pages_via_pypdf(pdf_path: str):
    import pypdf
    reader = pypdf.PdfReader(pdf_path)
    return [(pg.extract_text() or "") for pg in reader.pages]


def _pages_via_pdftotext(pdf_path: str):
    import subprocess
    out = subprocess.run(["pdftotext", "-layout", pdf_path, "-"],
                         capture_output=True, timeout=180)
    if out.returncode != 0:
        raise RuntimeError(f"pdftotext exit {out.returncode}")
    # \f is pdftotext's page separator
    return out.stdout.decode("utf-8", "replace").split("\f")


def read_pdf_pages(pdf_path: str):
    """Page texts, trying three extractors in order.

    pdfplumber is the primary because its layout handling is what the retrieval
    and the two-column summary tables were tuned against. But pdfminer, which it
    sits on, raises "list index out of range" on certain malformed page trees --
    it did so for GU0171, GY0076 and PN0159 in the 2026-09-27 full run, which
    are ordinary 11-to-33-page PDFs that both pypdf and pdftotext read without
    complaint (130k-180k characters each). Falling back recovers them instead of
    recording a null row.

    Raises the ORIGINAL pdfplumber error if every extractor fails, so the audit
    still shows the real cause."""
    first_error = None
    for reader in (_pages_via_pdfplumber, _pages_via_pypdf, _pages_via_pdftotext):
        try:
            pages = reader(pdf_path)
        except Exception as e:
            if first_error is None:
                first_error = e
            continue
        if any((p or "").strip() for p in pages):
            return pages
        if first_error is None:
            first_error = ValueError("no text on any page")
    raise first_error if first_error else ValueError("no extractor produced text")


def extract_paragraphs(pdf_path: str):
    """Return a list of {para_id, page, text} for every numbered paragraph,
    plus a fallback 'page_blob' for pages with no numbered paragraphs
    (cover page, tables, annexes)."""
    _ensure_downloaded(pdf_path)
    if sniff_document_type(pdf_path) != "pdf":
        # A .pdf name is not a promise. AR-L1408's two "PDFs" in the 2026-09-27
        # run were 3.8 KB HTML error pages from a failed download; handing those
        # to pdfplumber produced "No /Root object!" instead of a clear reason.
        raise ValueError(f"file is named .pdf but its content is not a PDF "
                          f"(likely a failed download saving an error page)")
    paragraphs = []
    current = None
    for page_num, text in enumerate(read_pdf_pages(pdf_path), start=1):
            text = text or ""
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


def sniff_document_type(doc_path: str):
    """The file's REAL type from its first bytes: "pdf", "docx", or None.

    The extension cannot be trusted. The IDB serves documents named with their
    full title, and long titles are cut at 150 bytes, which amputates the
    ".pdf" -- e.g. "Brazil. Proposal for an individual loan for the Program for
    the Digital Transformation of the Government of the State of Piaui - Piaui
    Mais Digital.pd". Checked on the real corpus 2026-09-25: 99 readable files
    across 80 operations, and for 14 operations EVERY document, were being
    skipped as "unsupported" while being perfectly good PDFs.

    A DOCX is a zip, and so are .xlsx/.pptx, so the zip is opened and checked
    for word/document.xml rather than trusted on its magic bytes alone."""
    try:
        with open(doc_path, "rb") as fh:
            head = fh.read(4)
    except OSError:
        return None
    if head[:4] == b"%PDF":
        return "pdf"
    if head[:4] == b"PK\x03\x04":
        try:
            import zipfile
            with zipfile.ZipFile(doc_path) as z:
                if "word/document.xml" in z.namelist():
                    return "docx"
        except Exception:
            return None
    return None


def extract_paragraphs_any(doc_path: str):
    """Dispatches to the right extractor. The extension decides when it is one
    we know; otherwise the file's own first bytes do (see sniff_document_type).
    Raises ValueError only when the content is genuinely not a PDF or DOCX
    (.xlsx, .ppt, legacy .doc, images), so callers can log a clear skip."""
    ext = Path(doc_path).suffix.lower()
    kind = {".pdf": "pdf", ".docx": "docx"}.get(ext) or sniff_document_type(doc_path)
    if kind == "pdf":
        return extract_paragraphs(doc_path)
    if kind == "docx":
        return extract_paragraphs_docx(doc_path)
    raise ValueError(f"unsupported document type '{ext}' -- only {sorted(SUPPORTED_EXTENSIONS)} "
                      f"are parsed for text; this file's content will not be included")


# Table-of-contents and index lines ("Financiación de importaciones.......6")
# carry keywords but no content. They are never tagged, so never retrieved.
_TOC_RE = re.compile(r"(\.{5,}|…{2,}|_{5,})\s*\d")



def _is_toc(text: str) -> bool:
    return bool(_TOC_RE.search(text))


def tag_sections(paragraphs):
    """Cheap keyword match to bucket paragraphs into candidate sections.
    A paragraph can match more than one bucket; unmatched paragraphs are
    still kept (e.g. background/rationale) but deprioritized."""
    tagged = []
    for p in paragraphs:
        if _is_toc(p["text"]):
            tagged.append({**p, "tags": []})
            continue
        low = _norm(p["text"])
        counts = {tag: sum(1 for k in kws if k in low) for tag, kws in _NORM_KEYWORDS.items()}
        counts = {t: c for t, c in counts.items() if c}
        tagged.append({**p, "tags": list(counts), "tag_hits": counts})
    return tagged


def build_retrieval_bundle(tagged_paragraphs, max_chars=12000, source_name=None):
    """Stage-1 output: paragraphs matching ANY schema-relevant tag, concatenated
    with citations, capped so Stage 2's prompt stays small and cheap.

    When the relevant paragraphs exceed the budget, they are selected by
    relevance score rather than by position. Earlier versions kept paragraphs
    in document order until the budget ran out, which silently dropped the
    back half of long documents -- typically the risk, execution, and
    fiduciary sections. Selected paragraphs are still emitted in document
    order so the excerpt reads naturally.

    source_name, if given, is prefixed to every citation so that when multiple
    documents are combined (loan proposal + annexes) each fact can still be
    traced back to the specific file it came from."""
    relevant = [(i, p) for i, p in enumerate(tagged_paragraphs) if p["tags"]]

    def chunk_for(p):
        cite = f"{source_name} ¶{p['para_id']}" if source_name else f"¶{p['para_id']}"
        return f"[{cite}, p.{p['page']}] {p['text']}"

    def score(p):
        return sum(TAG_WEIGHTS.get(t, 1) for t in p["tags"])

    ranked = sorted(relevant, key=lambda ip: (-score(ip[1]), ip[0]))
    chosen, used = [], 0
    for i, p in ranked:
        chunk = chunk_for(p)
        if used + len(chunk) > max_chars:
            continue
        chosen.append((i, chunk))
        used += len(chunk)
    chosen.sort()
    return "\n\n".join(c for _, c in chosen), len(relevant)


_NUMBERED_RE = re.compile(r"^\d{1,2}\.\d{1,2}$")

# Annexes dedicated to one topic are the best source for that topic's fields:
# the safeguard screening form states the environmental category, the
# procurement plan states the methods, and so on. A paragraph from such a
# document is ranked first within the matching tag.
_DOC_TYPE_HINTS = {
    "safeguards": ["safeguard", "spf", "esmr", "esms", "igas", "ambiental", "environmental"],
    "procurement": ["procurement", "adquisicion", "aquisic", "fiduciar"],
    "disbursement": ["disbursement", "desembolso"],
    "financing": ["financial", "financier", "costo", "cost"],
    "works": ["economic analysis", "analisis economico", "cost benefit", "costo beneficio",
              "evaluacion economica", "analise economica"],
}


def _doc_hints(filename: str):
    low = _norm(filename)
    return {t for t, kws in _DOC_TYPE_HINTS.items() if any(k in low for k in kws)}

# Sparse fields are served first in each selection round, so that their few
# paragraphs are never crowded out by the dense financing and risk sections.
_TAG_ORDER = ["price_adjustment", "eligibility", "execution_conditions", "cost_table",
              "works", "disbursement", "safeguards",
              "procurement", "execution", "financing", "risks"]


# The project summary page at the front of every loan proposal states the
# financing terms (amount, sources, currency, disbursement period) in one
# place. It is unnumbered and long, so ordinary ranking pushes it down; it is
# selected first instead.
# The financing-terms block is what identifies the real summary page; the
# words "project summary" alone also appear in the table of contents.
_SUMMARY_MARKERS = [_norm(t) for t in [
    "financial terms and conditions", "terminos y condiciones financieras",
    "termos e condicoes financeiras",
]]


def _is_summary_page(text: str) -> bool:
    head = _norm(text[:800])
    return any(m in head for m in _SUMMARY_MARKERS) and not _is_toc(text)


# Some "paragraphs" run to tens of thousands of characters, when a numbered
# paragraph absorbs the unnumbered annex text that follows it. One of these
# can consume the whole budget. Long paragraphs are cut to a window around
# their first keyword match, keeping the citation id.
MAX_PARA_CHARS = 2500


def _window(text: str, tags) -> str:
    if len(text) <= MAX_PARA_CHARS:
        return text
    low = _norm(text)
    positions = [low.find(k) for t in tags for k in _NORM_KEYWORDS.get(t, []) if k in low]
    start = max(0, min(positions) - 400) if positions else 0
    # _norm can shift offsets slightly (combining marks removed); the window
    # is generous enough that this does not matter.
    piece = text[start:start + MAX_PARA_CHARS]
    return ("[...] " if start else "") + piece + " [...]"


def select_across_documents(candidates, max_chars):
    """Choose paragraphs from ALL of a project's documents under one budget.

    candidates: list of dicts with keys doc_idx, pos, tags, chunk.

    Selection is tag-balanced: each round takes the next best paragraph for
    every tag in turn, so every schema-relevant section gets space before any
    section gets a second paragraph. The project summary page, if found, is
    taken before anything else (only the first, since proposals often come in two languages). Within a tag, paragraphs from a document
    dedicated to that topic come first (see _DOC_TYPE_HINTS), then those matching more of
    that tag's keywords come first, then numbered paragraphs (the body of the
    loan proposal) over unnumbered annex text, then earlier documents.

    This replaces an earlier scheme that capped each document separately and
    then truncated the concatenation by position, which dropped whole
    sections from later documents whenever the total exceeded the budget."""
    queues = {}
    for i, c in enumerate(candidates):
        for t in c["tags"]:
            queues.setdefault(t, []).append(i)
    order = [t for t in _TAG_ORDER if t in queues] + sorted(t for t in queues if t not in _TAG_ORDER)
    for t in order:
        queues[t].sort(key=lambda i, t=t: (t not in candidates[i].get("doc_hints", ()),
                                           -candidates[i].get("hits", {}).get(t, 1),
                                           not candidates[i]["numbered"],
                                           candidates[i]["doc_idx"], candidates[i]["pos"]))
    chosen, used = set(), 0
    for i in sorted((i for i, c in enumerate(candidates) if c.get("pinned")),
                    key=lambda i: (candidates[i]["doc_idx"], candidates[i]["pos"]))[:1]:
        if used + len(candidates[i]["chunk"]) + 2 <= max_chars:
            chosen.add(i)
            used += len(candidates[i]["chunk"]) + 2
    active = list(order)
    while active:
        still = []
        for t in active:
            q = queues[t]
            while q and (q[0] in chosen or used + len(candidates[q[0]]["chunk"]) + 2 > max_chars):
                q.pop(0)
            if q:
                i = q.pop(0)
                chosen.add(i)
                used += len(candidates[i]["chunk"]) + 2
                if q:
                    still.append(t)
        active = still
    return sorted(chosen, key=lambda i: (candidates[i]["doc_idx"], candidates[i]["pos"]))


def build_project_bundle(doc_paths, max_chars_per_doc=None, max_chars_total=24000):
    """Run Stage 1 across every document belonging to one project (loan
    proposal + annexes -- PDF and DOCX alike) and select the most relevant
    paragraphs across all of them under a single character budget (see
    select_across_documents). Files of an unsupported type are skipped with a
    note at the top of the bundle rather than silently dropped. Returns
    (combined_text, total_paras, total_relevant, failed_documents) -- the
    last one is a list of {file, error} for anything that raised while
    parsing, so a caller can tell "genuinely no relevant paragraphs" apart
    from "this document was never actually read."

    max_chars_per_doc is accepted for backward compatibility and ignored."""
    notes, candidates, total_paras, total_relevant, skipped, failed = [], [], 0, 0, [], []
    for doc_idx, doc_path in enumerate(doc_paths):
        name = Path(doc_path).name
        try:
            size_mb = Path(doc_path).stat().st_size / (1024 * 1024)
        except OSError:
            size_mb = 0
        if size_mb > MAX_FILE_SIZE_MB:
            skipped.append(name)
            notes.append(f"[{name}] -- skipped, {size_mb:.0f} MB exceeds the "
                         f"{MAX_FILE_SIZE_MB} MB parsing limit")
            print(f"[note] {name}: {size_mb:.0f} MB exceeds MAX_FILE_SIZE_MB "
                  f"({MAX_FILE_SIZE_MB}) -- skipping to avoid the OOM risk large scanned "
                  f"PDFs pose", file=sys.stderr)
            continue
        try:
            paras = extract_paragraphs_any(doc_path)
        except ValueError as e:
            skipped.append(name)
            notes.append(f"[{name}] -- skipped, not parsed for text: {e}")
            continue
        except Exception as e:
            failed.append({"file": name, "error": str(e)})
            notes.append(f"[{name}] -- could not parse: {e}")
            continue
        total_paras += len(paras)
        for pos, p in enumerate(tag_sections(paras)):
            pinned = _is_summary_page(p["text"])
            if not p["tags"] and not pinned:
                continue
            total_relevant += 1
            candidates.append({
                "doc_idx": doc_idx, "pos": pos, "tags": p["tags"],
                "hits": p.get("tag_hits", {}),
                "doc_hints": _doc_hints(name),
                "pinned": pinned,
                "numbered": bool(_NUMBERED_RE.match(str(p["para_id"]))),
                "chunk": f"[{name} ¶{p['para_id']}, p.{p['page']}] {_window(p['text'], p['tags'] or ['financing'])}",
            })
    if skipped:
        print(f"[note] {len(skipped)} file(s) not parsed (unsupported type): {', '.join(skipped)}",
              file=sys.stderr)
    if failed:
        print(f"[warn] {len(failed)} file(s) FAILED to parse (see stats.failed_documents): "
              f"{', '.join(f['file'] for f in failed)}", file=sys.stderr)
    header = "\n".join(notes)
    budget = max_chars_total - len(header) - 2
    picked = select_across_documents(candidates, budget)
    body = "\n\n".join(candidates[i]["chunk"] for i in picked)
    combined = f"{header}\n\n{body}" if header and body else (header or body)
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
