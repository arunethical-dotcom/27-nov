"""Product identifiers and their page-local source spans."""
from dataclasses import dataclass
from datetime import date
import re


@dataclass(frozen=True)
class SourceValue:
    value: str
    chunk_id: int
    start: int
    end: int


FIELDS = {
    "product_name": r"^Product name:[ \t]*(\S[^\n]*?)\s*$",
    "product_code": r"^Product code:[ \t]*(\S[^\n]*?)\s*$",
    "revision_date": r"^Revision date:[ \t]*(\d{4}-\d{2}-\d{2})\b",
    "cas_numbers": r"(?<![\d-])(\d{2,7}-\d{2}-\d)(?![\d-])",
}


def extract_metadata(rows):
    result = {field: [] for field in FIELDS}
    for row in rows:
        if row.method != "section" or row.section.split(".")[0] not in {"0", "1"}:
            continue
        for field, pattern in FIELDS.items():
            for match in re.finditer(pattern, row.text, re.I | re.M):
                value = match[1]
                if field == "revision_date":
                    date.fromisoformat(value)
                result[field].append(SourceValue(value, row.id, row.start + match.start(1),
                                                row.start + match.end(1)))
    return result
