from app.chunking import chunks


def test_measurements_belong_to_physical_properties():
    page = "SECTION 9: Physical properties\n9.1 Physical state\n0.85 g/cm3 at 20 C\n1.2 kg per litre\n"
    rows = [r for r in chunks([page], 1) if r.method == "section"]
    assert [r.section for r in rows] == ["9", "9.1"]
    assert "0.85 g/cm3 at 20 C\n1.2 kg per litre" in rows[1].text
