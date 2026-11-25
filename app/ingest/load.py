"""Load local demo PDFs into the advisor database."""
import argparse
from datetime import date
from hashlib import sha256
from pathlib import Path

from sqlalchemy import event, func, text
from sqlmodel import Session, create_engine, select

from app.chunking import chunks, document_text, page_starts
from app.db import Chunk, Product, SDSVersion, initialize_database
from app.ingest import page_texts
from app.ingest.metadata import extract_metadata


def upgrade_legacy_offsets(db, version, pages):
    """Preserve historical chunk IDs and citations when their original PDF returns."""
    starts = page_starts(pages)
    for row in db.exec(select(Chunk).where(Chunk.version_id == version.id)):
        if not 1 <= row.page <= len(pages) or pages[row.page - 1][row.start:row.end] != row.text:
            raise ValueError("Legacy chunk does not match the original PDF")
        row.start += starts[row.page - 1]
        row.end += starts[row.page - 1]
        row.page_start = row.page_end = row.page
    version.document_text = document_text(pages)


def load_directory(directory):
    engine = create_engine("sqlite:///advisor.db")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    initialize_database(engine)
    pending = []
    for path in sorted(directory.glob("*.pdf")):
        digest = sha256(path.read_bytes()).hexdigest()
        with Session(engine) as db, db.begin():
            db.execute(text("BEGIN IMMEDIATE"))
            existing = db.exec(select(SDSVersion).where(SDSVersion.file_hash == digest)).first()
            if existing:
                if existing.document_text is None:
                    upgrade_legacy_offsets(db, existing, page_texts(path))
                print(f"{path.name}: duplicate")
                continue
        pages = page_texts(path)
        rows = list(chunks(pages, 0))
        metadata = extract_metadata(rows)
        fields = {}
        for key in ("product_code", "revision_date", "product_name"):
            candidates = metadata[key]
            values = {item.value for item in candidates}
            if len(values) != 1 or any(item.ambiguous for item in candidates):
                print(f"{path.name}: needs manual metadata (missing {key})")
                break
            fields[key] = values.pop()
        else:
            pending.append((fields["product_code"], date.fromisoformat(fields["revision_date"]),
                            path.name, digest, fields["product_name"],
                            sorted({v.value for v in metadata["cas_numbers"]}), document_text(pages), rows))

    for code, revision, filename, digest, name, cas, source, rows in sorted(pending):
        with Session(engine) as db, db.begin():
            db.execute(text("BEGIN IMMEDIATE"))
            if db.exec(select(SDSVersion.id).where(SDSVersion.file_hash == digest)).first():
                print(f"{filename}: duplicate")
                continue
            product = db.exec(select(Product).where(Product.code == code)).first()
            if product is None:
                product = Product(code=code, name=name, cas_numbers=cas)
                db.add(product)
                db.flush()
            previous = db.exec(select(func.max(SDSVersion.version_no)).where(
                SDSVersion.product_id == product.id)).one() or 0
            number = previous + 1
            active = db.exec(select(SDSVersion).where(
                SDSVersion.product_id == product.id, SDSVersion.status == "active")).first()
            status = "pending" if previous else "active"
            message = "pending: needs user confirmation" if previous else "active"
            if active and active.revision_date and revision <= active.revision_date:
                message = "older or same revision date: needs user confirmation"
            version = SDSVersion(product_id=product.id, version_no=number,
                                 revision_date=revision, file_hash=digest, status=status,
                                 uploaded_by="demo", document_text=source)
            db.add(version)
            db.flush()
            for row in rows:
                row.version_id = version.id
            db.add_all(rows)
        print(f"{filename}: {message}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    if not args.directory.is_dir():
        parser.error("Expected a directory of PDF files")
    load_directory(args.directory)
