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

**Current output: 2,581 operations across 25 borrowing countries, approval years
1992 through 2026, 17 extracted fields, every non-missing value paired with a
paragraph citation and a verbatim supporting quote.** 1,843 of those operations
have both a document-derived record and a disbursement outcome in the Bank's own
data warehouse, which is the sample an econometric specification can use.

## Design

The pipeline runs in four stages, separating deterministic text processing from
model-based extraction so that each can be validated independently.

**Stage 0 — Harvest.** `src/harvest_projects.py` walks the IDB public project
search, filters to approved and closed Investment Loans and Policy-Based Loans,
and downloads each operation's loan proposal and annexes
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
| `executing_agency_type` | 2,438 | 94.5% |
| `cofinancing_present` | 2,430 | 94.1% |
| `disbursement_period_years` | 2,391 | 92.6% |
| `civil_works_type` | 2,358 | 91.4% |
| `conditions_prior_first_disbursement` | 2,350 | 91.0% |
| `fx_denomination` | 2,324 | 90.0% |
| `counterpart_funding_share_pct` | 2,245 | 87.0% |
| `procurement_modality` | 1,850 | 71.7% |
| `cofinancing_share_pct` | 1,759 | 68.2% |
| `first_year_disbursement_pct` | 1,354 | 52.5% |
| `safeguards_category` | 1,332 | 51.6% |
| `retroactive_financing` | 974 | 37.7% |
| `construction_share_pct` | 745 | 28.9% |
| `advance_contracting` | 342 | 13.3% |
| `goods_share_pct` | 186 | 7.2% |
| `price_escalation_clause` | 126 | 4.9% |
| `eligibility_date` | 40 | 1.5% |

141 operations (5.5%) have no extracted values. For 111 of those the Bank
published no loan proposal at all, only a completion report or a procurement
plan, so the absence is a property of the public record rather than of the
pipeline. The remaining 30 have documents that yield no extractable text.

Two of the low-coverage fields are low for a substantive reason rather than a
recoverable one. `goods_share_pct` sits at 7% because IDB cost tables are
organised by project component, not by expenditure category, so a goods share is
not stated in most proposals. `eligibility_date` sits at 1.5% because a proposal
states an expected date rather than the declared one; the warehouse records the
declared date for every operation, at day precision, and should be used instead.
`safeguards_category` at 52% reflects a change in Bank practice: the
environmental classification was not standard in proposals written before about
2003, which is where a large part of this corpus now sits.

## Validation

The extraction is checked two ways, because most of the fields have no external
benchmark and it is worth being explicit about which claims rest on what.

**Against the Bank's data warehouse.** Three of the 17 fields have a warehouse
counterpart. On the environmental classification, where both sources assert a
substantive category, they agree on 838 of 859 operations, or 97.6%. Every one of
the 21 disagreements is between adjacent categories, 16 of them between the two
middle ones. A further 99 cases have the extraction reporting a category while
the warehouse records the "no classification required" default, against 2 cases
in the opposite direction; a 50-to-1 asymmetry indicates an incomplete warehouse
field rather than an invented value. On disbursement period the two measures
correlate at 0.918 across 1,991 operations, with the extraction larger in 97% of
cases by a median of four months, which is the definitional gap between the span
stated in the proposal and the warehouse's commitment period.

**Against hand-coding.** The remaining 14 fields have no external source
anywhere, which is the reason they are worth extracting and also the reason a
warehouse comparison cannot speak to them.
`src/sample_for_validation.py` draws a sample stratified by field and status, so
that `found` values are not swamped by the `not_stated` rows that dominate a flat
draw, and `src/score_validation.py` scores a reviewer's entries with type-aware
comparison. That measurement is in progress and its result will be reported here
rather than summarised.

## Engineering notes

Several defects found during the full run are worth recording, because each was
silent and each cost data.

**Two operation-numbering schemes.** The Bank changed its numbering around 2003.
Operations approved before then carry a country prefix and four digits
(`AR0058`); after, a prefix, an instrument letter and three to five digits
(`CO-L1234`). Five separate call sites encoded only the second pattern, including
the harvest filter and the check that flags a quote naming a different operation.
The consequence was not an error but an absence: 709 operations with complete
disbursement histories in the warehouse were never harvested, and the
contamination check was blind to every one of the older operations. The patterns
now live in one module, `src/opnum.py`, with the country prefixes taken from the
Bank's own export rather than assumed.

**Filenames truncated past their extension.** The Bank names documents with their
full title, and long titles are cut at 150 bytes, which removes the `.pdf`. 99
readable documents across 80 operations were being skipped as unsupported, and
for 14 operations every document they had. Document type is now determined from
the file's first bytes rather than its name.

**Citation resolution by quote, not by identifier.** Paragraph identifiers are
not unique across an operation's documents: an unnumbered page is labelled
`page-6` in every file. An earlier resolver matched on the identifier and
attributed a value to whichever document happened to be indexed first, which was
wrong about as often as it was right. Resolution is now by verbatim quote, with
the identifier as a fallback only where it is unique, and a value whose source
cannot be established is recorded as unresolved rather than guessed.

**Malformed tool calls.** A forced tool call occasionally returns with arguments
missing. Three such calls appeared in roughly 41,500 field values. Unguarded,
one of them ended a run that had already processed 149 operations. Malformed
calls are now dropped individually and recorded in the audit.

## Repository layout

```
src/                      pipeline modules, one per stage
src/opnum.py              canonical operation-number patterns, imported everywhere
src/tests/                unit test and captured fixtures
data/processed/           the extracted panel, all 2,581 operations
data/audit/               a 50-operation excerpt of the citation-and-quote audit
                          (the full audit is 23 MB; Stage 3 regenerates it)
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

**Live search pagination is unvalidated.** `scrape_search_results` has never been
run against the production site, and no part of the current corpus came through
it. The two supported paths are an export of the project list from the IDB search
page, passed with `--project-list`, and an explicit operation list passed with
`--only-operations`, which bypasses the year and instrument screens. The
569-operation extension that brought the corpus back to 1996 used the second.
Anyone reaching for the scraper should smoke-test it with `--limit` first.

**Fourteen of the seventeen fields have no measured accuracy.** The three fields
with a warehouse counterpart are validated above. For the other fourteen there is
no external benchmark, and fill rate is not accuracy. A stratified sample of 225
field-values has been drawn and is being hand-coded; until that is scored, the
coverage table should be read as a statement about what the documents contain,
not about whether the extraction read them correctly.

**Two panel fields are not fit for the use their names suggest.**
`eligibility_date` appears for 40 operations because proposals state an expected
date rather than the declared one — the warehouse field should be used instead.
`goods_share_pct` appears for 186 because IDB cost tables are organised by
component rather than by expenditure category. Both are kept because a stated
value is still informative; neither should be used as a panel variable.

## In progress

The hand-coded validation study: scoring the drawn sample for field-level
precision and recall with confidence intervals, building an error taxonomy from
the observed failures, and testing whether error rates vary systematically by
document language, length, and vintage.

## Author

Johanna Fajardo-Gonzalez, Ph.D. — applied microeconomist.
https://sites.google.com/view/jfg-econ

## License

MIT. The underlying IDB loan proposals are public documents published by the
Inter-American Development Bank and are subject to the Bank's own terms.
