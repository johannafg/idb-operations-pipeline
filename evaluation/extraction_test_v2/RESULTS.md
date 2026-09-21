# Extraction re-test: 11 operations after cross-document selection

The 11 operations affected by the first extraction test were re-run after the
changes described in `../bundle_test/RESULTS.md`. Output is in `panel_new.csv`
and `audit_new.json`.

| field | original run | first test | this re-test |
|---|---:|---:|---:|
| `safeguards_category` | 3 | 6 | 10 |
| `procurement_modality` | 6 | 7 | 10 |
| `disbursement_period_years` | 0 | 10 | 11 |
| `executing_agency_type` | 9 | 11 | 11 |
| `construction_share_pct` | 4 | 3 | 5 |
| `price_escalation_clause` | 1 | 2 | 3 |
| `counterpart_funding_share_pct` | 5 | 10 | 9 |
| `fx_denomination` | 7 | 10 | 9 |
| `cofinancing_present` | 6 | 8 | 7 |
| `cofinancing_share_pct` | 2 | 5 | 3 |
| `imported_inputs_present` | 4 | 5 | 4 |
| **total found** | **56** | **86** | **91** |

The targeted recoveries worked. BA-L1012 now has its safeguard category (B) and
loan currency, EC-L1121 its procurement methods from a body paragraph rather
than a glossary, and DR-L1080 its construction share. The table-of-contents and
imported-disease false positives on imported inputs are gone.

## New regression: the project summary page

Financing fields fell slightly. The losses trace to the project summary page at
the front of each loan proposal, which states the financing terms in one table:
amount, sources, currency, disbursement period. It is unnumbered and long, and
the new ranking pushed it out of the excerpt for AR-L1436, BL-L1020, and
UR-L1070. DR-L1080 kept its summary page and still lost two financing values,
which indicates some run-to-run variation in the model's output independent of
retrieval.

Two further causes were found while fixing this. Some parsed "paragraphs" run to
tens of thousands of characters, when a numbered paragraph absorbs the
unnumbered annex text that follows it; one of them could consume most of the
budget. And the words "project summary" also appear in the table of contents, so
a first attempt at pinning the summary page selected the contents page instead.

## Imported inputs

Precision on `imported_inputs_present` remains poor. Of the four values found,
one clearly describes project inputs (UR-L1070, budgeted import costs), one is
borderline (BA-L1012, a public-private partnership to import natural gas), and
two do not (AR-L1436, where the province's imported inputs arrive by road; and
EC-L1045, where the quote is about import duties paid by the airline). Retrieval
changes have reached their limit on this field. It needs either a stricter
extraction prompt with worked examples or removal from the analysis panel.
`imported_share_est` is still never found.
