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
    ("goods_share_pct", "number or null",
     "Share of total project cost budgeted for goods, equipment, machinery or vehicles, as "
     "distinct from civil works, consulting services, and administration. Read it from the "
     "cost-and-financing or component cost table: divide the goods/equipment line by the total "
     "project cost and give a percentage. If the table separates several goods lines, sum them. "
     "If no line is identifiably goods or equipment, use null rather than inferring from the "
     "project description."),
    ("first_year_disbursement_pct", "number or null",
     "Percentage of the loan projected to be disbursed in the first year, taken from the "
     "projected disbursement schedule (often a table of Year 1, Year 2, ... amounts or shares). "
     "If the table gives amounts rather than shares, divide the first-year amount by the total. "
     "If the schedule is semiannual or quarterly, sum the first four quarters or first two "
     "semesters. Use null if no disbursement schedule is present."),
    ("conditions_prior_first_disbursement", "string or null",
     "Special contractual conditions that must be met before the FIRST disbursement, beyond the "
     "standard requirement that the loan contract be signed and declared eligible. Summarise "
     "them briefly (e.g. 'execution unit staffed and operations manual approved'). Use "
     "stated_absent if the document states there are no special conditions precedent; null if "
     "the document does not address them."),
    ("retroactive_financing", "boolean or null",
     "Does the document state that the Bank may retroactively finance eligible expenditures "
     "incurred by the borrower before the loan contract is signed? Spanish and Portuguese: "
     "'financiamiento retroactivo', 'financiamento retroativo'. Use null if not addressed."),
    ("advance_contracting", "boolean or null",
     "Does the document state that procurement or contracting may begin before the loan contract "
     "is signed, at the borrower's own risk? Spanish and Portuguese: 'contratación anticipada', "
     "'adquisiciones anticipadas', 'contratação antecipada'. Distinguish from retroactive "
     "financing, which concerns reimbursement of expenditure rather than the timing of "
     "procurement. Use null if not addressed."),
    ("eligibility_date_stated", "string (YYYY-MM-DD) or null",
     "The date on which the loan becomes eligible for the borrower to request the first "
     "disbursement -- distinct from the Board approval date (the date the loan is authorized) "
     "and the signature/contract date (the date the legal agreement is signed). Look for "
     "language like 'the eligibility date is estimated at/set for...', 'first eligible for "
     "disbursement on...', or an explicit 'Eligibility Date' field in a conditions/execution "
     "table. If only a relative eligibility condition is stated (e.g. '90 days after "
     "signature') without a calendar date, use null rather than computing one -- this field is "
     "only for an explicit, document-stated calendar date. This is the panel's preferred hazard-"
     "model spell start (see docs/Methodology_Hazard_LP_ShiftShare.tex, Section 1); "
     "approval_date is used as a fallback only where this is null."),
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


def build_prompt(retrieval_bundle: str, operation_number: str = None) -> str:
    field_list = "\n".join(f"- {name} ({typ}): {desc}" for name, typ, desc in FIELD_DEFS)
    subject = (f"IDB operation {operation_number}" if operation_number
               else "a single IDB operation")
    return f"""You are extracting structured project-characteristic data about {subject},
from an excerpt of its Loan Proposal and annexes, for use in an econometric panel. The excerpt below already contains only the
paragraphs judged relevant by a retrieval step; each paragraph is tagged with a citation id in
brackets, e.g. [¶2.9, p.21] or [page-6, p.6].

Extract the following fields. For EACH field, call the `record_extraction` tool exactly once.
Rules:
- Only use information present in the excerpt below. Do not use outside knowledge about this project.
- status="found" only if the value is directly supported by a quoted passage.
- status="stated_absent" if the document explicitly says the thing does not apply / is zero / does not exist.
- status="not_stated" if the excerpt simply never addresses the field -- in that case value must be null
  and quote may be empty, but you must still call the tool for that field.
- citation_para must be copied EXACTLY as it appears in the excerpt's brackets, including
  the file name and page, e.g. "Loan Proposal.pdf ¶2.9, p.21". Do not shorten it to the
  paragraph number alone, and do not invent one.
- The excerpt is assembled from the loan proposal AND its annexes. Annexes sometimes
  describe OTHER operations or earlier programs (impact studies, portfolio reviews,
  comparisons). Use only text that refers to {subject}. If a passage states a
  value for a different operation or programme, treat this field as not_stated rather
  than borrowing that value. Dated tables in evaluation annexes are the common trap.
- Many operations are supplementary financings, second phases, or individual loans under a
  CCLIP or multi-phase program. Their documents narrate the ORIGINAL or PARENT operation at
  length -- its cost, its cofinanciers, its executing arrangements -- often naming it only by
  programme acronym (e.g. "the MACRP", "the Program") and not by operation number. Facts about
  the original, parent, earlier, or first-phase operation are NOT facts about {subject}. If the
  only evidence for a field comes from a passage describing that earlier operation, return
  not_stated. Where the excerpt contains a cost-and-financing or project-summary table for
  {subject} itself, that table governs over any narrative about the predecessor.
- Do not guess or infer beyond what the text supports.
- Never use a table-of-contents entry, heading, index line, or annex title as evidence.

FIELDS:
{field_list}

DOCUMENT EXCERPT:
---
{retrieval_bundle}
---
"""
