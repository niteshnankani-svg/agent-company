"""The one result shape every department returns.

A department never returns a bare string, a dict with department-specific keys,
or a made-up number. It returns a ``DepartmentResult``. Everything downstream
(verifier, API, human review, UI) reads only this shape.

Rules the model enforces, so a department cannot get them wrong silently:

* ``answered``        -> there is an answer AND at least one piece of evidence.
* ``needs_human``     -> there is a machine-readable ``reason`` for the reviewer.
* ``needs_more_info`` -> ``missing_fields`` says exactly what to ask the user.
* ``failed``          -> ``error`` says, in words, what broke.
* confidence is declared with a *kind*. ``none`` means the department has no
  real number and must not invent one.
"""
from __future__ import annotations

import math
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

Status = Literal["answered", "needs_human", "needs_more_info", "failed"]
ConfidenceKind = Literal["calibrated_probability", "retrieval_score", "none"]


class Evidence(BaseModel):
    """One piece of material a department relied on.

    ``source`` says where it came from (a service, a document, a rule set).
    ``text`` and/or ``data`` hold the material itself, not a label for it.
    """

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    text: Optional[str] = None
    data: Optional[dict[str, Any]] = None

    @model_validator(mode="after")
    def has_content(self) -> "Evidence":
        if not (self.text and self.text.strip()) and not self.data:
            raise ValueError(
                "evidence must carry the actual text or data relied on, not just a label"
            )
        return self


class Confidence(BaseModel):
    """A number plus a statement of what the number is.

    * ``calibrated_probability``: a probability whose calibration has been
      measured. (No department can claim this today.)
    * ``retrieval_score``: how similar retrieved material is to the question.
      NOT the probability that an answer is right.
    * ``none``: there is no real number. ``value`` must be None.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ConfidenceKind = "none"
    value: Optional[float] = None

    @model_validator(mode="after")
    def value_matches_kind(self) -> "Confidence":
        if self.kind == "none":
            if self.value is not None:
                raise ValueError("confidence kind 'none' must not carry a value")
            return self
        if self.value is None or not math.isfinite(self.value):
            raise ValueError(f"confidence kind '{self.kind}' needs a finite value")
        if self.kind == "calibrated_probability" and not 0.0 <= self.value <= 1.0:
            raise ValueError("a calibrated probability must be between 0 and 1")
        return self


NO_CONFIDENCE = Confidence(kind="none", value=None)


class DepartmentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department: str = Field(min_length=1)
    status: Status
    answer: Optional[str] = None
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: Confidence = Field(default_factory=lambda: NO_CONFIDENCE)
    missing_fields: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    # Why the request is not simply answered: a short code a human or a UI can
    # act on, e.g. "security_incident_review". Required for needs_human.
    reason: Optional[str] = None

    @model_validator(mode="after")
    def status_invariants(self) -> "DepartmentResult":
        s = self.status
        if s == "answered":
            if not (self.answer and self.answer.strip()):
                raise ValueError("status 'answered' requires a non-empty answer")
            if not self.evidence:
                raise ValueError("status 'answered' requires at least one piece of evidence")
            if self.error:
                raise ValueError("status 'answered' must not carry an error")
        elif s == "needs_human":
            if not self.reason:
                raise ValueError("status 'needs_human' requires a reason")
        elif s == "needs_more_info":
            if not self.missing_fields:
                raise ValueError("status 'needs_more_info' requires missing_fields")
        elif s == "failed":
            if not (self.error and self.error.strip()):
                raise ValueError("status 'failed' requires an error message")
        if s != "needs_more_info" and self.missing_fields:
            raise ValueError("missing_fields is only valid with status 'needs_more_info'")
        return self

    # --- state boundary -------------------------------------------------
    # Graph state holds plain JSON-able dicts so it can be checkpointed
    # (Phase 5). These two helpers are the only places that convert.
    def to_state(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_state(cls, data: dict[str, Any]) -> "DepartmentResult":
        return cls.model_validate(data)


def truncate(text: Any, limit: int = 300) -> str:
    """Bound free text from a remote service before it goes into ``error``."""
    s = str(text)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def service_error(service: str, payload: dict) -> str:
    """Turn a wrapper's ``{"error": ..., "detail": ...}`` into a short reason."""
    code = payload.get("error", "unknown_error")
    detail = payload.get("detail")
    return f"{service}: {code}" + (f" ({truncate(detail)})" if detail else "")
