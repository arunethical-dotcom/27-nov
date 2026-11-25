import pytest
from app.chunking import chunks


@pytest.mark.parametrize("heading", [
    "SECTION 7 - Handling", "Section 7 Handling",
    "7. HANDLING AND STORAGE", "7 Handling and storage",
])
def test_storage_heading_formats(heading):
    page = heading + "\n7 barrels in storage\n17 Handling and storage"
    rows = [r for r in chunks([page], 1) if r.method == "section"]
    assert [(r.section, r.text) for r in rows] == [("7", page)]
