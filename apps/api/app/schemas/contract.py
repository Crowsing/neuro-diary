"""Pydantic documentation for the local-only web JSON contract (schema v4).

There are intentionally no synchronization endpoints. The models mirror exported
web state so a future opt-in transport cannot silently reinterpret observations.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", strict=True)


class SymptomExtra(ContractModel):
    label: str
    opts: list[str] = Field(default_factory=list)
    multi: bool


class SymptomDef(ContractModel):
    id: str
    name: str
    type: Literal["scale", "bool"]
    side: list[str] | None = None
    extra: SymptomExtra | None = None
    episodes: bool | None = None
    impact: bool | None = None
    cat: str | None = None


class SymValue(ContractModel):
    # The JSON key is literally "int"; the Python name avoids shadowing int.
    intensity: int | None = Field(default=None, alias="int")
    side: str | None = None
    extra: list[str] | None = None
    ep: int | None = None
    impact: str | None = None
    comment: str | None = None
    more: bool | None = None


class Ctx(ContractModel):
    stress: int | None = None
    sleepQ: int | None = None
    sleepH: float | None = None
    activity: bool | None = None
    actType: str = ""
    heat: bool | None = None
    extras: list[str] = Field(default_factory=list)


class Flare(ContractModel):
    isNew: bool = False
    dur24: bool = False
    temp: bool = False
    note: str = ""
    groupIds: list[str] | None = None


class DoneEntry(ContractModel):
    status: Literal["done"]
    wb: int | None = None
    sym: dict[str, SymValue] = Field(default_factory=dict)
    absent: list[str] = Field(default_factory=list)
    confirmed: bool | None = None
    ctx: Ctx = Field(default_factory=Ctx)
    note: str = ""
    flare: Flare | None = None
    noSymptoms: bool = False
    legacyNoSymptoms: bool | None = None
    filledLater: bool = False

    @model_validator(mode="after")
    def _present_never_overlaps_absent(self) -> DoneEntry:
        """§13.13: `sym ∩ absent = ∅`, the tri-state invariant of §4.3.

        The asymmetry with the client is deliberate and documented: `apps/web`
        repairs such an entry on read (it must never lose a diary to a bad
        write), while the active contract refuses it — a value that is both
        present and explicitly absent has no meaning to agree on.
        """
        overlap = set(self.absent) & set(self.sym)
        if overlap:
            raise ValueError("absent must not intersect sym")
        return self


class CheckinDraft(ContractModel):
    wb: int | None = None
    wbSkip: bool = False
    sel: list[str] = Field(default_factory=list)
    sym: dict[str, SymValue] = Field(default_factory=dict)
    absent: list[str] = Field(default_factory=list)
    groupId: str | None = None
    ctx: Ctx = Field(default_factory=Ctx)
    note: str = ""
    flare: Flare | None = None
    confirmed: bool = False
    noSymptoms: bool = False
    legacyNoSymptoms: bool | None = None
    step: int = 1
    symIdx: int | None = None
    ctxMore: bool | None = None


class DraftEntry(ContractModel):
    status: Literal["draft"]
    d: CheckinDraft


Entry = Annotated[DoneEntry | DraftEntry, Field(discriminator="status")]


class TrackingGroup(ContractModel):
    id: str
    name: str
    archived: bool = False


class AppData(ContractModel):
    schemaVersion: Literal[4] = 4
    entries: dict[str, Entry] = Field(default_factory=dict)
    cycleStarts: list[str] = Field(default_factory=list)
    active: list[str] = Field(default_factory=list)
    archived: list[str] = Field(default_factory=list)
    custom: list[SymptomDef] = Field(default_factory=list)
    groups: list[TrackingGroup] = Field(default_factory=list)
    symptomGroupIds: dict[str, list[str]] = Field(default_factory=dict)
    cycleOn: bool = False
    lock: bool = False


class LegacyAppDataInput(AppData):
    """Input adapter for persisted v4 data with retired reminder fields."""

    @model_validator(mode="before")
    @classmethod
    def discard_reminder_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        current = value.copy()
        current.pop("remOn", None)
        current.pop("remTime", None)
        return current


class ReportState(ContractModel):
    step: Literal[1, 2, 3] = 1
    period: Literal[7, 30, 90] = 30
    groupIds: list[str] = Field(default_factory=list)
    syms: list[str] = Field(default_factory=list)
    includeGroupNames: bool = False
    ctx: bool = True
    cycle: bool = False
    notes: bool = False
    flares: bool = True
    name: str = ""
    dob: str = ""


class TrendsState(ContractModel):
    period: Literal[7, 30, 90] = 30
    mode: Literal["chart", "table"] = "chart"
    sym: str = ""
    groupId: str | None = None
    sel: int | None = None


class CheckinState(ContractModel):
    date: str
    d: CheckinDraft
    back: Literal["day"] | None = None


class ExportState(ContractModel):
    """State emitted by the web JSON exporter; dialog and toast are excluded."""

    view: Literal["ob", "app"] = "ob"
    obStep: int = 0
    obSyms: list[str] | None = Field(default_factory=list)
    obCycle: bool = False
    safetyMore: bool = False
    tab: Literal["today", "history", "trends", "report", "set"] = "today"
    sub: (
        Literal[
            "checkin", "day", "sym", "catalog", "groups", "privacy", "safety", "crisis"
        ]
        | None
    ) = None
    checkin: CheckinState | None = None
    selDay: str | None = None
    histShift: int = 0
    histFilter: Literal["all", "flare", "menses", "notes", "draft"] = "all"
    historyGroupId: str | None = None
    catQ: str = ""
    catFrom: Literal["set", "checkin"] | None = None
    catalogGroupId: str | None = None
    newName: str = ""
    newType: Literal["bool", "scale"] = "bool"
    newGroupIds: list[str] = Field(default_factory=list)
    groupName: str = ""
    groupError: str = ""
    editingGroupId: str | None = None
    trends: TrendsState = Field(default_factory=TrendsState)
    crisisAns: Literal["", "yes", "no"] = ""
    report: ReportState = Field(default_factory=ReportState)
    data: AppData = Field(default_factory=AppData)


class LegacyExportStateInput(ExportState):
    """Input adapter for exported state with retired reminder fields."""

    data: LegacyAppDataInput = Field(default_factory=LegacyAppDataInput)

    @model_validator(mode="before")
    @classmethod
    def discard_reminder_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        current = value.copy()
        current.pop("obRemOn", None)
        current.pop("obTime", None)
        return current
