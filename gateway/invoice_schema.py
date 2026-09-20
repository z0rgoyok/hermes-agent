"""Versioned extraction contract, independent of the agent and provider transport."""
from decimal import Decimal
from datetime import date as CalendarDate
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RecognitionError(Exception):
    def __init__(self, reason, *, transient=False, auth=False):
        super().__init__(reason)
        self.transient, self.auth = transient, auth


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Uncertainty(StrictModel):
    field: str
    original: str | None = None
    alternatives: list[str] = Field(default_factory=list)
    explanation: str


class InvoiceExtractionV1(StrictModel):
    schema_version: Literal[1] = 1
    document_type: Literal["invoice", "other", "unreadable"]
    client: str | None
    date: CalendarDate | None
    total: Decimal | None = Field(allow_inf_nan=False)
    uncertainties: list[Uncertainty]
    visual_evidence: str


def arithmetic_warnings(card: InvoiceExtractionV1) -> list[str]:
    # Only the printed grand total is extracted; no invented line-item arithmetic.
    return ["negative total"] if card.total is not None and card.total < 0 else []
