"""Load local demo PDFs into the advisor database."""
import argparse
from datetime import date
from hashlib import sha256
from pathlib import Path

from sqlalchemy import event, func, text
from sqlmodel import Session, SQLModel, create_engine, select

from app.chunking import chunks
from app.db import Product, SDSVersion
from app.ingest import page_texts
from app.ingest.metadata import extract_metadata


def load_directory(directory):
    engine = create_engine("sqlite:///advisor.db")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)
    pending = []
    for path in sorted(directory.glob("*.pdf")):
        digest = sha256(path.read_bytes()).hexdigest()
        with Session(engine) as db:
            if db.exec(select(SDSVersion.id).where(SDSVersion.file_hash == digest)).first():
                print(f"{path.name}: duplicate")
                continue
        rows = list(chunks(page_texts(path), 0))
        metadata = extract_metadata(rows)
        def field(key):
            values = {v.value for v in metadata[key]}
            if len(values) != 1:
                raise ValueError(f"{path.name}: missing or ambiguous {key}")
            return values.pop()
        pending.append((field("product_code"), date.fromisoformat(field("revision_date")),
                        path.name, digest, field("product_name"),
                        sorted({v.value for v in metadata["cas_numbers"]}), rows))

    for code, revision, filename, digest, name, cas, rows in sorted(pending):
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
                                 uploaded_by="demo")
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
