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
SENTENCE = re.compile(
    r"\S.*?(?:[.!?](?=\s|$)|(?=\n[ \t]*(?:\n|[-*•]\s|\d+[.)]\s))|(?=\s*\Z))",
    re.DOTALL,
)


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
    for page, origin, source in content_spans(pages, furniture_config):
        headings = list(HEADING.finditer(source))
        boundaries = [(0, section)]
        top = section.split(".")[0] if section else None
        for match in headings:
            number = match["label"] or match["number"]
            if number:
                if match["title"] and not match["title"].lower().startswith(TITLE_WORDS[int(number) - 1]):
                    continue
                top = number
                boundaries.append((match.start(), top))
            elif match["sub"].split(".")[0] == top:
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
                for match in SENTENCE.finditer(body):
                    yield chunk("sentence", *match.span())
