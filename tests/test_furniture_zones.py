from app.chunking import chunks


def test_repeated_storage_evidence_survives_midpage():
    pages = [
        "Banner\nFirst page\nSECTION 10: Stability\n10.5 Incompatible materials:\n"
        "No data available.\n1 / 2\nAdditional details\nEnd of notes\nPage 1 of 2\n",
        "Banner\nSecond page\nContinued\nNo data available.\nMore details\n"
        "See product label\nEnd of sheet\nPage 2 of 2\n",
    ]
    rows = [r for r in chunks(pages, 1) if r.method == "section"]
    for number, page in enumerate(pages, 1):
        evidence = [r for r in rows if r.page == number and r.section == "10.5"
                    and "No data available." in r.text]
        assert len(evidence) == 1
        assert page[evidence[0].start:evidence[0].end] == evidence[0].text
    assert all("Banner" not in r.text and "Page " not in r.text for r in rows)
    assert any("1 / 2" in r.text for r in rows)
