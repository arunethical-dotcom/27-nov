from app.chunking import chunks


def test_storage_sentence_preserves_wrap():
    text = "7.2 Conditions for safe storage: Do not store together with\nstrong oxidising agents or Boreal 200."
    page = "SECTION 7: Handling\n" + text + "\n"
    rows = [r for r in chunks([page], 1) if r.method == "sentence" and r.section == "7.2"]
    assert [r.text for r in rows] == [text]
    assert page[rows[0].start:rows[0].end] == text
