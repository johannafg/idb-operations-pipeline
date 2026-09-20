# IDB Operations Microdata Pipeline

A staged document-extraction pipeline that converts public Inter-American
Development Bank (IDB) loan proposals into a structured, citation-backed panel
of project characteristics suitable for econometric analysis.

Development-finance research is constrained by the coarseness of available
project metadata. Country, sector, and approval amount are published in
machine-readable form; the project-level characteristics that actually drive
execution risk are not. Cofinancing arrangements, counterpart funding shares,
procurement modality, currency denomination, price-escalation provisions, and
executing-agency type are stated in the loan proposal text and coded nowhere.
This pipeline extracts them.

**Current output: 1,217 operations across 25 borrowing countries, 12 extracted
fields, every non-missing value paired with a paragraph citation and a verbatim
supporting quote.**

## Design

The pipeline runs in four stages, separating deterministic text processing from
model-based extraction so that each can be validated independently.

**Stage 0 — Harvest.** `src/harvest_projects.py` walks the IDB public project
search, filters to approved and closed Investment Loans and Policy-Based Loans
for 1998 through 2019, and downloads each operation's loan proposal and annexes
into `corpus/<OPERATION-NUMBER>/`. Pre-approval proposals are confidential until
Board approval, so the approved-and-closed filter is a correctness requirement
rather than a convenience. Requests are rate-limited.

**Stage 1 — Parse and retrieve.** `src/parse_loan_proposal.py` splits each
document into numbered paragraphs. IDB proposals number every paragraph, which
supplies stable citation anchors without any inference. A retrieval step then
narrows the document to the paragraphs relevant to the extraction schema. For
the CO-L1234 test case this reduces 105 paragraphs to 37, which cuts token cost
and, more importantly, confines the model to text that has already been judged
on-topic by deterministic rules.

**Stage 2 — Extract.** `src/schema.py` defines the field set and the extraction
contract. The model must call `record_extraction` exactly once per field and
must return four things together: the value, a paragraph citation drawn from the
bracketed identifiers in the excerpt, a verbatim quote, and a status flag with
three levels.

| status | meaning |
|---|---|
| `found` | the value is directly supported by the quoted passage |
| `stated_absent` | the document explicitly states the thing does not apply, is zero, or does not exist |
| `not_stated` | the document never addresses the field |

The three-level status flag is the central design decision. A conventional
extraction returns a value or a null, which conflates two different situations:
the document was silent, and the model failed. Separating them is what makes the
output auditable and what makes measured error rates interpretable. A null
carrying `stated_absent` is information. A null carrying `not_stated` is an
absence of information. Only a disagreement on a `found` value is a model error.

**Stage 3 — Assemble.** `src/build_microdata_panel.py` loops extraction over the
corpus and produces one row per operation keyed on operation number, plus a JSON
audit log holding every citation and quote behind every cell. Batch planning
(`src/plan_batch.py`) skips operations already complete, so interrupted runs
resume without re-billing finished work.

**Stage 4 — Report.** `src/build_excel_report.py` produces a reviewer-facing
workbook pairing each extracted cell with its source quote, so a subject-matter
reviewer can verify any value without touching the pipeline.

## Coverage

Fill rates vary sharply across fields, and the variation is itself a finding.
Fields describing the financing structure and the executing arrangement are
recoverable from most documents. Fields describing input composition and
contractual price provisions are not, because loan proposals rarely state them
in extractable form.

| field | operations with a value | share |
|---|---:|---:|
| `executing_agency_type` | 955 | 78.5% |
| `civil_works_type` | 926 | 76.1% |
| `cofinancing_present` | 865 | 71.1% |
| `fx_denomination` | 808 | 66.4% |
| `counterpart_funding_share_pct` | 749 | 61.5% |
| `procurement_modality` | 442 | 36.3% |
| `cofinancing_share_pct` | 435 | 35.7% |
| `safeguards_category` | 261 | 21.4% |
| `construction_share_pct` | 75 | 6.2% |
| `imported_inputs_present` | 7 | 0.6% |
| `price_escalation_clause` | 1 | 0.1% |
| `imported_share_est` | 0 | 0.0% |

190 operations (15.6%) have no extracted values at all, concentrated in older
vintages where the archived document is a scanned image rather than digital
text.

The three lowest-coverage fields should be treated as unusable in their current
form. Whether they are unrecoverable in principle or recoverable with a revised
retrieval step is an open question and the first item in the evaluation work
described below.

## Repository layout

```
src/                      pipeline modules, one per stage
src/tests/                unit test and captured fixtures
data/processed/           the extracted panel
data/audit/               citations and quotes behind every cell
data/reference/           harvest metadata (country, instrument, approval date)
evaluation/               validation design and measured accuracy (in progress)
analysis/                 findings and figures (in progress)
```

The document corpus is roughly 13 GB and is not distributed here. It is
reproducible from the public IDB project search using Stage 0.

## Reproducing

```bash
pip install -r requirements.txt
cp .env.example .env          # add an Anthropic API key
export $(cat .env | xargs)

cd src
python3 tests/test_harvest_parser.py          # sanity check

python3 harvest_projects.py --project-list "Project Information.xlsx" \
    --corpus-root ../corpus --start-year 1998 --end-year 2019

python3 build_microdata_panel.py ../corpus \
    --out ../data/processed/microdata_panel_latest.csv

python3 build_excel_report.py \
    --panel ../data/processed/microdata_panel_latest.csv \
    --audit microdata_audit.json \
    --out ../data/processed/microdata_panel_latest.xlsx \
    --harvest-log ../data/reference/harvest_log.csv
```

Extraction cost is roughly a few cents per operation, so a full run over the
sample is well under fifty dollars.

## Known issues

`disbursement_period_years` is defined in `src/schema.py` but does not appear in
the current panel, which was built before the field was added. It requires a
re-run.

The live search pagination in `scrape_search_results` has not been validated
against the production site and should be smoke-tested with `--limit` before any
full run. The supported path is to export the project list from the IDB search
page and pass it with `--project-list`.

Extraction has not yet been validated against hand-coded ground truth. Field
fill rates are reported above, but fill rate is not accuracy. Measured precision
and recall are the next deliverable.

## In progress

A validation study drawing a stratified sample of operations, hand-coding the
target fields, and reporting field-level precision and recall with confidence
intervals, an error taxonomy built from observed failures, and a test of whether
error rates vary systematically by document language, length, and vintage.

## Author

Johanna Fajardo-Gonzalez, Ph.D. — applied microeconomist.
https://sites.google.com/view/jfg-econ

## License

MIT. The underlying IDB loan proposals are public documents published by the
Inter-American Development Bank and are subject to the Bank's own terms.
