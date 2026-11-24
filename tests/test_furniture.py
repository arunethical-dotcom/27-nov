from pathlib import Path
import pytest
from app.chunking import chunks
from app.ingest import page_texts


@pytest.mark.parametrize("path", sorted(Path("fixtures").glob("*.pdf")), ids=lambda p:p.stem)
def test_section_16_excludes_footer(path):
    rows = [r for r in chunks(page_texts(path), 1) if r.section == "16"]
    assert rows
    assert all("Software evaluation fixture" not in r.text and "1 / 1" not in r.text for r in rows)


def test_furniture_cuts_preserve_page_offsets():
    pages = ["SECTION 7: Handling\nFirst line.\nBanner\nKeep dry.\nPage 1 of 2\n",
             "Other storage notes.\nBanner\nClose lid.\n2 / 2\n"]
    rows = list(chunks(pages, 1))
    assert {r.method for r in rows} == {"section", "window", "sentence"}
    for row in rows:
        assert row.text == pages[row.page-1][row.start:row.end]
        assert not any(x in row.text for x in ("Banner", "Page 1 of 2", "2 / 2"))
    assert any("Close lid." in r.text for r in rows)
