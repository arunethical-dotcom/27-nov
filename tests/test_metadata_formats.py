import pytest
from app.db import Chunk
from app.ingest.metadata import extract_metadata


def metadata(text):
    row = Chunk(id=12, version_id=1, section="1", page=1, method="section",
                start=70, end=70 + len(text), text=text)
    return extract_metadata([row])


@pytest.mark.parametrize("label", ["Revision Date", "Date of revision", "Revised"])
@pytest.mark.parametrize("raw,value,ambiguous", [
    ("2026-03-11", "2026-03-11", False), ("23/03/2026", "2026-03-23", False),
    ("23.03.2026", "2026-03-23", False), ("11 March 2026", "2026-03-11", False),
    ("11/03/2026", "11/03/2026", True), ("11.03.2026", "11.03.2026", True),
    ("03/03/2026", "03/03/2026", True),
])
def test_revision_formats_and_raw_source(label, raw, value, ambiguous):
    text = f"{label}: {raw}"
    item, = metadata(text)["revision_date"]
    assert (item.value, item.raw, item.ambiguous) == (value, raw, ambiguous)
    assert item.chunk_id == 12
    assert text[item.start - 70:item.end - 70] == raw


@pytest.mark.parametrize("label", ["Product name", "Product identifier", "Trade name"])
def test_product_aliases(label):
    item, = metadata(f"{label}: Example solvent")["product_name"]
    assert item.value == "Example solvent"


def test_cas_checksum():
    items = metadata("CAS: 64-17-5, 64-17-6, 67-64-1")["cas_numbers"]
    assert [i.value for i in items] == ["64-17-5", "67-64-1"]

@pytest.mark.parametrize("label,raw,field", [
    ("Revision Date", "03/11/2026", "revision_date"),
    ("Revised", "03/03/2026", "revision_date"),
    ("Product identifier", "Example solvent", "product_name"),
    ("Trade name", "Example solvent", "product_name"),
    ("Product code", "EX-100", "product_code"),
])
def test_whitespace_delimited_metadata(label, raw, field):
    text = f"{label} {raw}"
    item, = metadata(text)[field]
    assert (item.value, item.raw) == (raw, raw)
    assert item.ambiguous == (field == "revision_date")
    assert text[item.start - 70:item.end - 70] == raw
