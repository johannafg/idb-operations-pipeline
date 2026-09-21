"""
Stage 2 schema: the extraction agent returns one object per field, always
paired with a citation and a status flag distinguishing 'found', 'stated as
absent', and 'not stated' -- this is the guardrail discussed in the
methodology memo (Section 2.2).
"""

FIELD_DEFS = [
    ("cofinancing_present", "boolean",
     "Is this operation co-financed by another multilateral, bilateral, or private lender?"),
    ("cofinancing_share_pct", "number or null",
     "Co-financed amount as a percent of total project cost, if computable from the text."),
    ("imported_inputs_present", "boolean or null",
     "Does the document explicitly state that goods, equipment, or materials financed by "
     "THIS project will be imported from outside the borrowing country? Only a statement "
     "about the project's own inputs counts. Do NOT count: exclusion lists (e.g. the "
     "project will not finance the import of pesticides), procurement-method or "
     "bidding-threshold rules such as international competitive bidding, delays, or "
     "outsourced services, assumptions in an economic analysis (e.g. shadow prices of "
     "imported fuel), trade statistics, or imported disease cases. Use null if the "
     "document is silent on this."),
    ("imported_share_est", "string or null",
     "Estimated share of procurement that is imported, if stated (else null)."),
    ("civil_works_type", "string or null",
     "Category of physical works described (e.g., roads, rail, water/sanitation, energy, buildings, none)."),
    ("construction_share_pct", "number or null",
     "Share of first-operation budget allocated to civil works / inspections vs. TA or consulting."),
    ("procurement_modality", "string or null",
     "Procurement modalities named (e.g., ICB, NCB, direct contracting, shopping)."),
    ("counterpart_funding_share_pct", "number or null",
     "Share of project cost financed by the borrower/counterpart, distinct from MDB or private financing."),
    ("fx_denomination", "string or null",
     "Currency the loan is approved/disbursed in, and whether conversion to local currency is planned."),
    ("price_escalation_clause", "boolean or null",
     "Does the document state that works or supply contracts include (or will include) an "
     "explicit price-adjustment, price-escalation, or price-redetermination mechanism for "
     "input costs? Spanish and Portuguese equivalents include 'redeterminación de precios', "
     "'fórmula polinómica', 'fórmulas paramétricas', 'reajuste de precios', and "
     "'reajuste de preços'. A mechanism proposed as a risk mitigation action counts. "
     "Distinguish from generic 'risk allocation in the contract' language."),
    ("executing_agency_type", "string or null",
     "Type of executing agency (national government, subnational, autonomous SOE, etc.) and whether newly created."),
    ("safeguards_category", "string or null",
     "Environmental and social risk category (A/B/C) under the Bank's safeguards policy."),
    ("disbursement_period_years", "number or null",
     "Total planned disbursement period in years, from the first to the final projected "
     "disbursement, as stated or computable from the disbursement schedule/table in the "
     "document (e.g. 'the disbursement period is five (5) years' -> 5; a schedule spanning "
     "years 1 through 4 -> 4). Do not confuse with the loan's overall repayment tenor/"
     "amortization period, which is a different, usually much longer, span."),
]

EXTRACTION_TOOL = {
    "name": "record_extraction",
    "description": "Record one structured field extracted from an IDB loan proposal, with citation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "field": {"type": "string", "enum": [f[0] for f in FIELD_DEFS]},
            "value": {"description": "The extracted value; null if not stated in the document."},
            "status": {"type": "string", "enum": ["found", "stated_absent", "not_stated"]},
            "citation_para": {"type": "string", "description": "Paragraph id or page id, e.g. '2.9' or 'page-6'."},
            "quote": {"type": "string", "description": "Verbatim supporting sentence(s) from the source text."},
        },
        "required": ["field", "value", "status", "citation_para", "quote"],
    },
}


def build_prompt(retrieval_bundle: str) -> str:
    field_list = "\n".join(f"- {name} ({typ}): {desc}" for name, typ, desc in FIELD_DEFS)
    return f"""You are extracting structured project-characteristic data from an excerpt of an
IDB Loan Proposal for use in an econometric panel. The excerpt below already contains only the
paragraphs judged relevant by a retrieval step; each paragraph is tagged with a citation id in
brackets, e.g. [¶2.9, p.21] or [page-6, p.6].

Extract the following fields. For EACH field, call the `record_extraction` tool exactly once.
Rules:
- Only use information present in the excerpt below. Do not use outside knowledge about this project.
- status="found" only if the value is directly supported by a quoted passage.
- status="stated_absent" if the document explicitly says the thing does not apply / is zero / does not exist.
- status="not_stated" if the excerpt simply never addresses the field -- in that case value must be null
  and quote may be empty, but you must still call the tool for that field.
- citation_para must be one of the bracketed ids from the excerpt, not invented.
- Do not guess or infer beyond what the text supports.
- Never use a table-of-contents entry, heading, index line, or annex title as evidence.

FIELDS:
{field_list}

DOCUMENT EXCERPT:
---
{retrieval_bundle}
---
"""
