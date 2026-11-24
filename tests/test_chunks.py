from pathlib import Path

import pytest
from app.chunking import chunks
from app.ingest import page_texts


@pytest.mark.parametrize("path", sorted(Path("fixtures").glob("*.pdf")), ids=lambda p:p.stem)
def test_fixture_offsets(path):
    pages = page_texts(path)
    rows = list(chunks(pages, 1))
    assert {row.section.split(".")[0] for row in rows} == set(map(str, range(1, 17)))
    assert {row.method for row in rows} == {"section", "window", "sentence"}
    for row in rows:
        assert pages[row.page - 1][row.start:row.end] == row.text
    assert any(row.section == "10.5" for row in rows)


def test_window_overlap():
    page = "SECTION 7: Storage\n" + " ".join(map(str, range(830)))
    windows = [r for r in chunks([page], 1) if r.method == "window"]
    assert len(windows) == 3
    assert windows[0].text.split()[-80:] == windows[1].text.split()[:80]
