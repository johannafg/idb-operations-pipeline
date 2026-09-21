# Cross-document selection: check of specific passages

The 20-operation extraction test showed six values lost and three false
positives on imported inputs. This check asks, for each case, whether the
passage behind the value reaches the excerpt the model sees. It makes no model
calls. `check_bundles.py` builds each operation's excerpt under the previous
Stage 1 (`_prev_parse.py`) and the revised one, and looks for the quote recorded
in the audit log. Results are in `results.jsonl`.

## Changes

One budget for all of an operation's documents, replacing a per-document cap
followed by truncation by position. Selection is tag-balanced: each round takes
the best remaining paragraph for every topic in turn. Within a topic,
paragraphs from a document dedicated to it (a safeguard screening form, an
economic analysis annex) rank first, then paragraphs matching more of the
topic's keywords. Keywords were added for safeguard categories, bidding methods,
disbursement periods, loan currency, and infrastructure investment.

Table-of-contents lines are never retrieved. A paragraph counts as being about
imported inputs only if it also refers to goods, equipment, materials, or their
purchase, and not if it concerns shadow prices or imported disease cases.

## Results

| operation | field | passage should be | before | after |
|---|---|---|:-:|:-:|
| AR-L1436 | price_escalation_clause | kept | yes | yes |
| SU-L1021 | price_escalation_clause | kept | yes | yes |
| EC-L1045 | imported_inputs_present | kept | yes | yes |
| UR-L1070 | imported_inputs_present | kept | yes | yes |
| BA-L1012 | safeguards_category | kept | no | yes |
| BL-L1020 | construction_share_pct | kept | no | yes |
| DR-L1080 | construction_share_pct | kept | no | yes |
| UR-L1032 | construction_share_pct | kept | yes | yes |
| BA-L1012 | fx_denomination | kept | no | no |
| EC-L1121 | procurement_modality | kept | no | no |
| BA-L1012 | imported_inputs_present | dropped | retrieved | dropped |
| BL-L1020 | imported_inputs_present | dropped | retrieved | dropped |
| CO-L1222 | imported_inputs_present | dropped | retrieved | dropped |

Eleven of thirteen checks pass. The two that fail rested on weak evidence in the
original run. The procurement value for EC-L1121 came from an acronym glossary;
the new excerpt contains body paragraphs describing international and national
competitive bidding, which support the same value. The currency value for
BA-L1012 was inferred from a capital-cost figure quoted in US dollars; the new
excerpt contains other US-dollar amounts. Both fields should still be extracted,
from better passages.

Whether the model extracts correctly from the new excerpts is tested separately.
