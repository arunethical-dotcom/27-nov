from app.chunking import chunks


def test_configured_footer_is_only_removed_at_page_edge(tmp_path):
    config = tmp_path / "furniture.yaml"
    config.write_text("furniture:\n  - Supplier footer\n")
    page = ("Top\nSECTION 7: Handling\nFirst detail\nSupplier footer\n"
            "Other detail\nLast detail\nSupplier footer\n1 / 1\n")
    rows = [r for r in chunks([page], 1, furniture_config=config) if r.method == "section"]
    assert sum(r.text.count("Supplier footer") for r in rows) == 1
    assert any("Supplier footer\nOther detail" in r.text for r in rows)
