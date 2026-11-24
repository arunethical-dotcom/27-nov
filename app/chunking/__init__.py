"""Page-local character spans in unmodified PyMuPDF text."""
from pathlib import Path
from functools import lru_cache
import re

import yaml
from tokenizers import Tokenizer

from app.db import Chunk

TITLE_WORDS = (
    "identification", "hazard", "composition", "first", "fire", "accidental",
    "handling", "exposure", "physical", "stability", "toxicological",
    "ecological", "disposal", "transport", "regulatory", "other",
)
HEADING = re.compile(
    r"^[ \t]*(?:SECTION[ \t]+(?P<label>1[0-6]|[1-9])(?=[ \t:.-]|$)"
    r"|(?P<number>1[0-6]|[1-9])\.?[ \t]+(?P<title>[A-Za-z]+)"
    r"|(?P<sub>(?:1[0-6]|[1-9])\.\d+)(?=[ \t]+[^\W\d_]))",
    re.MULTILINE | re.IGNORECASE,
)
MEASUREMENT_UNIT = re.compile(
    r"[ \t]*(?:[fpnumµμkMc]?g|[cmkµμn]?m|m?mol|[mµμ]?l|ppm|ppb|pa|kpa|bar|°[cf])"
    r"(?=\s|/|$)", re.I,
)
ABBREVIATION = re.compile(r"\b(?:e\.g|i\.e|etc|approx|incl|no|max|min|ca)\.$", re.I)
SENTENCE_BREAK = re.compile(r"[.!?](?=\s|$)|\n(?=[ \t]*(?:\n|[-*•]\s|\d+[.)]\s))")


def sentence_spans(body):
    start = 0
    for boundary in SENTENCE_BREAK.finditer(body):
        end = boundary.end()
        if boundary[0] == "." and ABBREVIATION.search(body, max(0, end - 8), end):
            continue
        segment = body[start:end]
        if segment.strip():
            yield start + len(segment) - len(segment.lstrip()), start + len(segment.rstrip())
        start = end
    tail = body[start:]
    if tail.strip():
        yield start + len(tail) - len(tail.lstrip()), start + len(tail.rstrip())


PAGE_NUMBER = re.compile(r"(?:Page\s+\d+\s+of\s+\d+|\d+\s*/\s*\d+)", re.I)


FURNITURE_CONFIG = Path(__file__).with_name("furniture.yaml")


def content_spans(pages, config):
    configured = set(yaml.safe_load(Path(config).read_text(encoding="utf-8"))["furniture"])
    zones = []
    labels = []
    for page in pages:
        lines = page.splitlines(keepends=True)
        nonempty = [i for i, line in enumerate(lines) if line.strip()]
        zone = set(nonempty[:3] + nonempty[-3:])
        zones.append(zone)
        labels.append({lines[i].strip() for i in zone})
    repeated = set.intersection(*labels) if len(labels) > 1 else set()
    for page_number, (page, zone) in enumerate(zip(pages, zones), 1):
        start = cursor = 0
        for index, line in enumerate(page.splitlines(keepends=True)):
            label = line.strip()
            if index in zone and (label in repeated or PAGE_NUMBER.fullmatch(label)
                                  or label in configured):
                if cursor > start:
                    yield page_number, start, page[start:cursor]
                start = cursor + len(line)
            cursor += len(line)
        if cursor > start:
            yield page_number, start, page[start:cursor]


WINDOW_SIZE = 200
WINDOW_OVERLAP = 40


@lru_cache(maxsize=1)
def embedding_tokenizer():
    tokenizer = Tokenizer.from_file(str(Path(__file__).with_name("tokenizer.json")))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    return tokenizer


def chunks(pages, version_id, *, window_size=WINDOW_SIZE, window_overlap=WINDOW_OVERLAP,
           furniture_config=FURNITURE_CONFIG):
    if not 0 <= window_overlap < window_size <= 200:
        raise ValueError("Expected 0 <= overlap < size <= 200")
    section = "0"
    last_subsection = {}
    for page, origin, source in content_spans(pages, furniture_config):
        headings = list(HEADING.finditer(source))
        boundaries = [(0, section)]
        top = section.split(".")[0] if section else None
        for match in headings:
            number = match["label"] or match["number"]
            if number:
                if int(number) <= int(top):
                    continue
                if match["title"] and not match["title"].lower().startswith(TITLE_WORDS[int(number) - 1]):
                    continue
                top = number
                boundaries.append((match.start(), top))
            elif match["sub"].split(".")[0] == top:
                sub = int(match["sub"].split(".")[1])
                if sub <= last_subsection.get(top, -1) or MEASUREMENT_UNIT.match(source, match.end()):
                    continue
                last_subsection[top] = sub
                boundaries.append((match.start(), match["sub"]))
        for i, (start, section) in enumerate(boundaries):
            end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(source)
            body = source[start:end]
            if not section or not body.strip():
                continue

            def chunk(method, left, right):
                return Chunk(version_id=version_id, method=method, section=section,
                             page=page, start=origin + start + left, end=origin + start + right,
                             text=body[left:right])

            yield chunk("section", 0, len(body))
            offsets = embedding_tokenizer().encode(body, add_special_tokens=False).offsets
            for j in range(0, len(offsets), window_size - window_overlap):
                last = min(j + window_size, len(offsets))
                yield chunk("window", offsets[j][0], offsets[last - 1][1])
                if last == len(offsets):
                    break
            if section.split(".")[0] in {"2", "6", "7", "8", "10"}:
                for left, right in sentence_spans(body):
                    yield chunk("sentence", left, right)
