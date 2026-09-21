# Extraction test: 20 operations, before and after

The revised pipeline was run on 20 operations drawn from the retrieval test
sample (`ops.txt`): the six operations discussed in `../error_analysis.md`, four
that previously retrieved no relevant paragraphs, three from English-language
borrowers, and seven from Spanish- and Portuguese-language borrowers. Output is in
`panel_new.csv` and `audit_new.json`. The comparison is against the same 20
operations in the original run.

## Coverage

| field | found, before | found, after |
|---|---:|---:|
| `executing_agency_type` | 13 | 20 |
| `disbursement_period_years` | 0 | 19 |
| `counterpart_funding_share_pct` | 7 | 18 |
| `fx_denomination` | 10 | 18 |
| `cofinancing_present` | 8 | 17 |
| `civil_works_type` | 14 | 17 |
| `procurement_modality` | 7 | 12 |
| `cofinancing_share_pct` | 2 | 10 |
| `safeguards_category` | 4 | 8 |
| `imported_inputs_present` | 4 | 5 |
| `construction_share_pct` | 4 | 4 |
| `price_escalation_clause` | 1 | 2 |
| `imported_share_est` | 0 | 0 |
| **total** | **74** | **150** |

`disbursement_period_years` was absent from the original run, which predates the
field. Excluding it, found values rise from 74 to 131.

## The known case

AR-L1436 now records a price-escalation clause, citing paragraph 3.1: "La acción
propuesta para lograr su mitigación es incluir en los contratos de obra fórmulas
paramétricas específicas de redeterminación de precios." The original run
recorded the field as not stated. The same operation now also yields its
executing agency, its cofinancing status, and an eight-year disbursement period.

## Values lost

Nine values found in the original run are not found in the new one. Three of
these are corrections or equivalents rather than losses. DR-L1031's
`imported_inputs_present` was previously set to true on the strength of an
exclusion list barring the import of invasive species; it is now correctly
recorded as stated absent. Two others moved from a found zero or "none" to
`stated_absent`, which carries the same meaning.

The remaining six are genuine regressions: a safeguards category, a procurement
modality, a currency, and three construction shares. The most likely cause is the
overall 24,000-character cap, which still truncates by position once the
per-document excerpts are concatenated. With more text retrieved per document,
the cap now binds more often, and passages from later documents are dropped.
Relevance-ranking across documents, not only within them, is the next fix.

## Precision on imported inputs

The tightened definition of `imported_inputs_present` did not fix its precision
problem. Of the five values found, two describe inputs the project will import:
aircraft purchased from a foreign manufacturer (EC-L1045) and budgeted import
costs (UR-L1070). Three do not: shadow pricing of imported fuels in an economic
analysis annex (BA-L1012), a table-of-contents entry (CO-L1222), and a reference
to imported COVID-19 cases (BL-L1020). Retrieval now surfaces many more passages
containing the word "imported," and the model accepts too many of them.

This field needs either a validation step that rejects quotes from economic
analysis annexes and tables of contents, or a narrower retrieval group. Until
then it should not be used in analysis. `imported_share_est` remains empty and
should be dropped from the schema or redefined.

## Status

On this sample, the retrieval changes roughly double coverage and fix the known
false negative. They introduce a small number of regressions traceable to the
cross-document cap, and they do not resolve the precision problem on imported
inputs. Coverage is not accuracy. Field-level precision and recall still require
a hand-coded validation sample.
