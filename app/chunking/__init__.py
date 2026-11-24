"""Page-local character spans in unmodified PyMuPDF text."""
import re

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


def chunks(pages, version_id):
    section = None
    for page, source in enumerate(pages, 1):
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
                             page=page, start=start + left, end=start + right,
                             text=body[left:right])

            yield chunk("section", 0, len(body))
            tokens = list(re.finditer(r"\S+", body))
            for j in range(0, len(tokens), 320):
                last = min(j + 400, len(tokens))
                yield chunk("window", tokens[j].start(), tokens[last - 1].end())
                if last == len(tokens):
                    break
            if section.split(".")[0] in {"2", "6", "7", "8", "10"}:
                for match in SENTENCE.finditer(body):
                    yield chunk("sentence", *match.span())
