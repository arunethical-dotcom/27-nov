from datetime import date, datetime, timezone

from sqlalchemy import JSON, CheckConstraint, Column, Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel


def now():
    return datetime.now(timezone.utc)


class Product(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    code: str = Field(unique=True)
    name: str
    synonyms: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    cas_numbers: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))


class SDSVersion(SQLModel, table=True):
    __tablename__ = "sds_version"
    __table_args__ = (
        UniqueConstraint("product_id", "version_no"),
        Index("one_active", "product_id", unique=True, sqlite_where=text("status = 'active'")),
        CheckConstraint("status IN ('pending','active','archived')"),
    )
    id: int | None = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="product.id")
    version_no: int
    revision_date: date | None = None
    file_hash: str = Field(unique=True)
    status: str = "pending"
    document_text: str | None = None
    reviewed_storage: list = Field(default_factory=list, sa_column=Column(JSON))
    reviewer: str | None = None
    uploaded_by: str
    uploaded_at: datetime = Field(default_factory=now)


class Chunk(SQLModel, table=True):
    __table_args__ = (CheckConstraint("method IN ('section','window','sentence')"),)
    id: int | None = Field(default=None, primary_key=True)
    version_id: int = Field(foreign_key="sds_version.id")
    method: str
    section: str
    page: int
    page_start: int | None = None
    page_end: int | None = None
    start: int
    end: int
    text: str

    def matches_source(self, document_text):
        return (document_text is not None and self.page_start is not None
                and 0 <= self.start < self.end <= len(document_text)
                and document_text[self.start:self.end] == self.text)


class Fact(SQLModel, table=True):
    __table_args__ = (CheckConstraint("type IN ('restriction_name','restriction_class','hazard_class','h_code','ppe','emergency','no_data')"),)
    id: int | None = Field(default=None, primary_key=True)
    version_id: int = Field(foreign_key="sds_version.id")
    type: str
    value: str
    chunk_id: int = Field(foreign_key="chunk.id")
    start: int | None = None
    end: int | None = None
    quote: str | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None


class Session(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=now)


class SessionProduct(SQLModel, table=True):
    version_id: int | None = Field(default=None, foreign_key="sds_version.id")
    __tablename__ = "session_product"
    session_id: int = Field(primary_key=True, foreign_key="session.id")
    product_id: int = Field(primary_key=True, foreign_key="product.id")


class PairResult(SQLModel, table=True):
    __tablename__ = "pair_result"
    __table_args__ = (
        CheckConstraint("a_version_id < b_version_id"),
        UniqueConstraint("session_id", "a_version_id", "b_version_id"),
    )
    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id")
    a_version_id: int = Field(foreign_key="sds_version.id")
    b_version_id: int = Field(foreign_key="sds_version.id")
    verdict: str
    rule_id: str
    evidence_json: list = Field(sa_column=Column(JSON, nullable=False))
    to_verify_json: list = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=now)


class ChangeReport(SQLModel, table=True):
    __tablename__ = "change_report"
    id: int | None = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="product.id")
    old_version_id: int = Field(foreign_key="sds_version.id")
    new_version_id: int = Field(foreign_key="sds_version.id")
    field_diff_json: dict = Field(sa_column=Column(JSON, nullable=False))
    affected_pairs_json: list = Field(sa_column=Column(JSON, nullable=False))
    confirmed_by: str
    confirmed_at: datetime = Field(default_factory=now)


def initialize_database(engine):
    """Add span fields to historical databases without guessing page origins."""
    with engine.begin() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        SQLModel.metadata.create_all(conn)
        for table, fields in {"sds_version": {"document_text": "TEXT", "reviewed_storage": "JSON", "reviewer": "TEXT"},
                              "session_product": {"version_id": "INTEGER REFERENCES sds_version(id)"},
                              "fact": {"start": "INTEGER", "end": "INTEGER", "quote": "TEXT"},
                              "chunk": {"page_start": "INTEGER", "page_end": "INTEGER"}}.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, kind in fields.items():
                if name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
