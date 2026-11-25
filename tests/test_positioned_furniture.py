from app.chunking import chunks


def test_protect_sentences_even_at_matching_edge_positions():
    pages = ["SECTION 10: Stability\n10.5 Incompatible materials:\nNo data available.\nSECTION 11: Toxicological information\n",
             "11.1 Acute toxicity\nNo data available.\nSECTION 12: Ecological information\nNo data available.\n"]
    rows = [r for r in chunks(pages, 1) if r.method == "section"]
    assert [r.section for r in rows if "No data available." in r.text] == ["10.5", "11.1", "12"]
    assert sum(r.text.count("No data available.") for r in rows) == 3


def test_strip_only_occurrences_at_a_shared_edge_position():
    pages = ["Banner\nAlpha\nLabel\nX\nLabel\nPage 1 of 2\n",
             "Banner\nLabel\nY\nZ\nLabel\nPage 2 of 2\n"]
    rows = [r for r in chunks(pages, 1) if r.method == "section"]
    assert sum(r.text.count("Label") for r in rows) == 2
    assert all("Banner" not in r.text and "Page " not in r.text for r in rows)
