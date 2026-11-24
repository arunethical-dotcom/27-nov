from app.chunking import chunks


def test_earlier_heading_number_is_storage_body_text():
    pages = ["SECTION 7: Handling\n7.2 Storage\n2 Hazard warnings on the label must stay visible.\nKeep away from acids.\n",
             "SECTION 8: Exposure controls\nUse gloves."]
    rows = [r for r in chunks(pages, 1) if r.method == "section"]
    assert [r.section for r in rows] == ["7", "7.2", "8"]
    assert "2 Hazard warnings on the label must stay visible.\nKeep away from acids." in rows[1].text
