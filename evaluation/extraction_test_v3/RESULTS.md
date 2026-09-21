# Extraction re-test 3: 11 operations after pinning the summary page

Same 11 operations as `../extraction_test_v2/`. Found values per field:

| field | original | test 1 | test 2 | test 3 |
|---|---:|---:|---:|---:|
| `procurement_modality` | 6 | 7 | 10 | 11 |
| `counterpart_funding_share_pct` | 5 | 10 | 9 | 11 |
| `fx_denomination` | 7 | 10 | 9 | 11 |
| `executing_agency_type` | 9 | 11 | 11 | 11 |
| `disbursement_period_years` | 0 | 10 | 11 | 11 |
| `civil_works_type` | 9 | 9 | 9 | 11 |
| `safeguards_category` | 3 | 6 | 10 | 10 |
| `cofinancing_present` | 6 | 8 | 7 | 8 |
| `construction_share_pct` | 4 | 3 | 5 | 5 |
| `imported_inputs_present` | 4 | 5 | 4 | 5 |
| `cofinancing_share_pct` | 2 | 5 | 3 | 3 |
| `price_escalation_clause` | 1 | 2 | 3 | 3 |
| `imported_share_est` | 0 | 0 | 0 | 0 |
| **total** | **56** | **86** | **91** | **100** |

Pinning the financing-terms summary page restored the financing values lost in
test 2: UR-L1070's loan currency, BL-L1020's counterpart share, DR-L1080's
cofinancing status. The gains from test 2 held: BA-L1012's safeguard category,
EC-L1121's procurement methods, DR-L1080's construction share, AR-L1436's
price-escalation clause.

Two construction shares found in test 2 (UR-L1070, BA-L1012) were not found in
test 3, with no change to the passages reaching the model for those fields.
This is run-to-run variation in the model's output, and it sets a floor on how
precisely coverage can be compared across runs of this size. Two other changes
from test 2 are recodings of zero cofinancing from a found zero to
`stated_absent`, which carry the same meaning.

## Imported inputs: not usable

Of the four operations coded true for `imported_inputs_present`, one rests on
goods the project will procure (UR-L1070, supply of equipment and materials).
The others rest on imported inputs to the regional economy (AR-L1436,
BL-L1020) and on a project whose purpose is importing natural gas (BA-L1012).
A fifth operation (DR-L1031) is coded false from an exclusion list, which says
nothing about the project's own inputs. Four rounds of retrieval and definition changes have not fixed
this field. The distinction it asks for, whether the project's own inputs are
imported, is rarely stated in loan proposals and is better obtained from
procurement plans or contract data. `imported_share_est` has never produced a
value. Both are candidates for removal from the schema.

## Conclusion

Across four runs on the same operations, found values rose from 56 to 100.
Retrieval is no longer the binding constraint for the eleven fields that are
stated in loan proposals. The next question is accuracy, which requires a
hand-coded validation sample.
