from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Frequency = Literal["daily", "weekly", "monthly", "yearly"]
Weekday = Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
OccurrenceScope = Literal["this", "future", "all"]


class RecurrenceRule(BaseModel):
    freq: Frequency
    interval: int = Field(default=1, ge=1, le=365)
    until: str | None = Field(default=None, description="Inclusive date, YYYY-MM-DD")
    count: int | None = Field(default=None, ge=1, le=5000)
    weekdays: list[Weekday] = Field(default_factory=list)


class EventBase(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    start_at: datetime
    end_at: datetime
    all_day: bool = False
    category: str = Field(default="其他", max_length=32)
    location: str = Field(default="", max_length=160)
    notes: str = Field(default="", max_length=4000)
    source: str = Field(default="manual", max_length=64)
    reminder_minutes: int | None = Field(default=None, ge=0, le=10080)
    recurrence: RecurrenceRule | None = None

    @model_validator(mode="after")
    def check_window(self) -> "EventBase":
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be later than start_at")
        return self


class EventCreate(EventBase):
    pass


class EventPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    start_at: datetime | None = None
    end_at: datetime | None = None
    all_day: bool | None = None
    category: str | None = Field(default=None, max_length=32)
    location: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=4000)
    source: str | None = Field(default=None, max_length=64)
    reminder_minutes: int | None = Field(default=None, ge=0, le=10080)
    recurrence: RecurrenceRule | None = None

    @model_validator(mode="after")
    def check_window(self) -> "EventPatch":
        if self.start_at is not None and self.end_at is not None:
            if self.end_at <= self.start_at:
                raise ValueError("end_at must be later than start_at")
        return self


class OccurrenceModifyRequest(BaseModel):
    occurrence_start: datetime
    scope: OccurrenceScope
    updates: EventPatch


class OccurrenceDeleteRequest(BaseModel):
    occurrence_start: datetime
    scope: OccurrenceScope
