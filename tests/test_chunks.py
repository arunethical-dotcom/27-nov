from pathlib import Path

import pytest
from app.chunking import chunks
from app.ingest import page_texts


@pytest.mark.parametrize("path", sorted(Path("fixtures").glob("*.pdf")), ids=lambda p:p.stem)
def test_fixture_offsets(path):
    from app.chunking import document_text
    pages = page_texts(path)
    rows = list(chunks(pages, 1))
    assert {row.section.split(".")[0] for row in rows} == set(map(str, range(0, 17)))
    assert {row.method for row in rows} == {"section", "window", "sentence"}
    for row in rows:
        assert document_text(pages)[row.start:row.end] == row.text
    assert any(row.section == "10.5" for row in rows)


def test_window_overlap():
    from app.chunking import embedding_tokenizer

    page = "SECTION 7: Storage\n" + " ".join(["hydroxyethyl-cellulose"] * 125)
    windows = [r for r in chunks([page], 1) if r.method == "window"]
    tokens = embedding_tokenizer().encode(page, add_special_tokens=False).offsets
    expected = []
    for i in range(0, len(tokens), 160):
        end = min(i + 200, len(tokens))
        expected.append((tokens[i][0], tokens[end - 1][1]))
        if end == len(tokens):
            break
    assert len(expected) > 3
    assert [(r.start, r.end) for r in windows] == expected
    short = [r for r in chunks([page], 1, window_size=12, window_overlap=3)
             if r.method == "window"]
    assert (short[0].end, short[1].start) == (tokens[11][1], tokens[9][0])
