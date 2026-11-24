"""Page-local character spans in unmodified PyMuPDF text."""
import re

from app.db import Chunk

HEADING = re.compile(r"(?im)^[ \t]*(?:SECTION[ \t]+(1[0-6]|[1-9])\s*[:.]|(\d{1,2}\.\d+)\b)")
SENTENCE = re.compile(r"\S.*?(?:[.!?](?=\s|$)|(?=\n|$))")


def chunks(pages, version_id):
    section = None
    for page, source in enumerate(pages, 1):
        headings = list(HEADING.finditer(source))
        boundaries = [(0, section)]
        boundaries.extend((m.start(), m.group(1) or m.group(2)) for m in headings)
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
