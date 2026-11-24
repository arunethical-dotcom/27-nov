"""Product identifiers and their page-local source spans."""
from dataclasses import dataclass
from datetime import datetime
import re


@dataclass(frozen=True)
class SourceValue:
    value: str
    chunk_id: int
    start: int
    end: int
    raw: str
    ambiguous: bool = False


FIELDS = {
    "product_name": r"^(?:Product name|Product identifier|Trade name)[ \t]*:[ \t]*(\S[^\n]*?)\s*$",
    "product_code": r"^Product code:[ \t]*(\S[^\n]*?)\s*$",
    "revision_date": r"^(?:Revision date|Date of revision|Revised)[ \t]*:[ \t]*([^\n\r]+?)[ \t]*$",
    "cas_numbers": r"(?<![\d-])(\d{2,7}-\d{2}-\d)(?![\d-])",
}


def revision_value(raw):
    """Preserve a date for review when the source permits two interpretations."""
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d %B %Y"):
        try:
            parsed = datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
        if pattern in {"%d/%m/%Y", "%d.%m.%Y"} and parsed.day <= 12 and parsed.day != parsed.month:
            return raw, True
        return parsed.isoformat(), False
    return raw, True


def valid_cas(value):
    digits = value.replace("-", "")
    checksum = sum(weight * int(digit) for weight, digit in enumerate(digits[-2::-1], 1))
    return checksum % 10 == int(digits[-1])


def extract_metadata(rows):
    result = {field: [] for field in FIELDS}
    for row in rows:
        if row.method != "section" or row.section.split(".")[0] not in {"0", "1"}:
            continue
        for field, pattern in FIELDS.items():
            for match in re.finditer(pattern, row.text, re.I | re.M):
                raw = match[1]
                if field == "cas_numbers" and not valid_cas(raw):
                    continue
                value, ambiguous = revision_value(raw) if field == "revision_date" else (raw, False)
                result[field].append(SourceValue(value, row.id, row.start + match.start(1),
                                                row.start + match.end(1), raw, ambiguous))
    return result
