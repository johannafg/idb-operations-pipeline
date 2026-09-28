"""Canonical IDB operation-number patterns. One definition, imported everywhere.

The IDB uses two numbering schemes and the pipeline has now lost data twice by
encoding only the newer one:

  old   AR0058, BR0216, PE0136     country prefix + 4 digits, no separator
                                   used for operations approved up to about 2003
  new   CO-L1234, AR-L1436         country prefix + '-' + instrument letter + 3-5 digits
                                   used from about 2003 onward

Country prefixes below are the 29 that actually appear in the IDB's own bulk
export (corpus/data.xlsx), not a guess. Instrument letters likewise: L is the
loan instrument, and G/J/M/Q/S/T/U/W/X cover grants, technical cooperations and
the rest, which is why LOAN_RE is separate from ANY_RE.

Usage:

    from opnum import ANY_RE, LOAN_RE, is_operation_number, is_loan_operation

    df = df[df["operation_number"].astype(str).str.match(ANY_RE, na=False)]
"""
import re

COUNTRY_PREFIXES = [
    "AR", "BA", "BH", "BL", "BO", "BR", "CA", "CH", "CO", "CR", "DR", "EC",
    "ES", "GU", "GY", "HA", "HO", "JA", "ME", "NI", "PE", "PN", "PR", "RG",
    "RS", "SU", "TT", "UR", "VE",
]
_CC = "|".join(COUNTRY_PREFIXES)

# Any operation, either scheme, any instrument.
ANY_PATTERN = rf"^(?:{_CC})(?:-[A-Z]\d{{3,5}}|\d{{4}})$"

# Loan operations only: the '-L' form, or the old form (which was loans and
# project preparation facilities; the instrument is not encoded in the number,
# so the old scheme cannot be narrowed here -- filter on the export's
# 'Project Type' / 'Lending Type' columns instead, as harvest_projects.py does).
LOAN_PATTERN = rf"^(?:{_CC})(?:-L\d{{3,5}}|\d{{4}})$"

# For finding operation numbers INSIDE free text (contamination checks).
# Anchored on the real country prefixes so a stray token like "EN1234" in a
# document does not register as an operation.
INTEXT_PATTERN = rf"\b(?:{_CC})(?:-[A-Z]\d{{3,5}}|\d{{4}})\b"

ANY_RE    = re.compile(ANY_PATTERN)
LOAN_RE   = re.compile(LOAN_PATTERN)
INTEXT_RE = re.compile(INTEXT_PATTERN)

def is_operation_number(s) -> bool:
    return bool(ANY_RE.match(str(s).strip().upper()))

def is_loan_operation(s) -> bool:
    return bool(LOAN_RE.match(str(s).strip().upper()))

def find_operations(text: str):
    """Every operation number appearing in free text, in order, de-duplicated."""
    return list(dict.fromkeys(INTEXT_RE.findall(str(text).upper())))

def is_old_scheme(s) -> bool:
    s = str(s).strip().upper()
    return bool(re.match(rf"^(?:{_CC})\d{{4}}$", s))
