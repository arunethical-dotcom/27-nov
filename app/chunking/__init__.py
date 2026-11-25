"""Exact character spans in the canonical SDS document text."""
from bisect import bisect_right
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
        zone = {}
        for position, index in enumerate(nonempty[:3]):
            zone.setdefault(index, set()).add(("top", position, lines[index].strip()))
        for position, index in enumerate(reversed(nonempty[-3:])):
            zone.setdefault(index, set()).add(("bottom", position, lines[index].strip()))
        zones.append(zone)
        labels.append(set().union(*zone.values()))
    repeated = set.intersection(*labels) if len(labels) > 1 else set()
    for page_number, (page, zone) in enumerate(zip(pages, zones), 1):
        start = cursor = 0
        for index, line in enumerate(page.splitlines(keepends=True)):
            label = line.strip()
            if index in zone and (PAGE_NUMBER.fullmatch(label) or label in configured
                                  or (not label.endswith(".") and zone[index] & repeated)):
                if cursor > start:
                    yield page_number, start, page[start:cursor]
                start = cursor + len(line)
            cursor += len(line)
        if cursor > start:
            yield page_number, start, page[start:cursor]


def page_separator(page):
    return "" if page.endswith("\n") else "\n"


def document_text(pages):
    """Keep each page verbatim, adding a line break only where necessary."""
    return "".join(page + page_separator(page) for page in pages[:-1]) + (pages[-1] if pages else "")


def page_starts(pages):
    starts = []
    cursor = 0
    for page in pages:
        starts.append(cursor)
        cursor += len(page) + len(page_separator(page))
    return starts


def uninterrupted_spans(pages, starts, config):
    """Join touching retained ranges, including a synthetic inter-page newline."""
    left = right = previous_page = None
    for page, local, source in content_spans(pages, config):
        begin = starts[page - 1] + local
        contiguous = begin == right
        if previous_page and page == previous_page + 1 and local == 0:
            contiguous = right == starts[previous_page - 1] + len(pages[previous_page - 1])
        if left is not None and not contiguous:
            yield left, right
            left = None
        if left is None:
            left = begin
        right = begin + len(source)
        previous_page = page
    if left is not None:
        yield left, right


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
    starts = page_starts(pages)
    document = document_text(pages)
    for origin, limit in uninterrupted_spans(pages, starts, furniture_config):
        source = document[origin:limit]
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
                begin = origin + start + left
                end_pos = origin + start + right
                first = bisect_right(starts, begin)
                last = bisect_right(starts, end_pos - 1)
                return Chunk(version_id=version_id, method=method, section=section,
                             page=first, page_start=first, page_end=last, start=begin, end=end_pos,
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
                    # A continued instruction can start after an inline subsection title.
                    if left == 0 and "." in section:
                        title = re.match(r"^\s*" + re.escape(section) + r"[ \t]+[^:\n]+:[ \t]+", body)
                        if title and title.end() < right:
                            instruction_start = origin + start + title.end()
                            if bisect_right(starts, instruction_start) < bisect_right(starts, origin + start + right - 1):
                                left = title.end()
                    yield chunk("sentence", left, right)
