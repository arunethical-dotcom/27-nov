from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

DISCLAIMER = "Decision support only. Confirm with a qualified person."


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Passage(Schema):
    chunk_id: int
    section: str
    quote: str
    page: int


class Evidence(Passage):
    side: Literal["A", "B"]


class ProductRef(Schema):
    code: str
    name: str


class Gap(Schema):
    product: ProductRef
    section: str
    reason: Literal["SDS_SAYS_NO_DATA", "NOT_LOCATED"]
    evidence: list[Passage] = Field(default_factory=list)


class Pair(Schema):
    a: ProductRef
    b: ProductRef
    a_version_id: int
    b_version_id: int
    abstained: bool
    verdict: Literal["CONFLICT", "UNRESOLVED", "NO_CONFLICT_FOUND"]
    rule_id: Literal["R1", "R2", "R3", "R4", "R5"]
    evidence: list[Evidence]
    to_verify: list[Gap]
    change_flag: Literal["NONE"] = "NONE"


class Chemical(Schema):
    product: ProductRef
    to_verify: list[Gap]
    sds_version: int
    ppe: list[Passage]
    emergency: dict[str, list[Passage]]
    storage_notes: list[Passage] = Field(default_factory=list)


class Advisory(Schema):
    session_id: str
    chemicals: list[Chemical]
    pairs: list[Pair]
    abstained: bool
    disclaimer: Literal["Decision support only. Confirm with a qualified person."] = DISCLAIMER
