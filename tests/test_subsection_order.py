from app.chunking import chunks


def test_measurement_and_decreasing_subsection_remain_in_body():
    page = ("SECTION 8: Exposure controls\n8.1 Control parameters\nTWA limit:\n"
            "8.5 mg per m3\n8.2 Exposure controls\n8.1 Control parameters repeated\n")
    rows = [r for r in chunks([page], 1) if r.method == "section"]
    assert [r.section for r in rows] == ["8", "8.1", "8.2"]
    assert "8.5 mg per m3" in rows[1].text
    assert "8.1 Control parameters repeated" in rows[2].text
