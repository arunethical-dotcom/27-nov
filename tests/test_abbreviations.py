import pytest
from app.chunking import chunks


@pytest.mark.parametrize("fragment", ["e.g. nitrates", "i.e. nitrates", "etc. during transfer",
                                      "approx. 2 kg", "incl. dust", "no. 8", "max. 5",
                                      "min. 3", "ca. 20", "2.75 kg"])
def test_abbreviations_and_decimals_inside_sentence(fragment):
    sentence = f"Keep away from oxidisers, {fragment} and peroxides."
    page = "SECTION 7: Handling\n7.2 Storage: " + sentence + " Close lid."
    rows = [r for r in chunks([page], 1) if r.method == "sentence" and r.section == "7.2"]
    assert [r.text for r in rows] == ["7.2 Storage: " + sentence, "Close lid."]
    assert all(r.text == page[r.start:r.end] for r in rows)
