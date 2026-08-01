"""Frozen version-1 command and query data-transfer contracts.

These types describe application intent. They deliberately contain no Streamlit,
SQL, pandas, or storage-driver objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import NewType

UserId = NewType("UserId", str)
ExperimentId = NewType("ExperimentId", str)
PlateId = NewType("PlateId", str)
RevisionId = NewType("RevisionId", str)


class AssayType(StrEnum):
    GROWTH = "growth"
    MIC = "mic"
    MIXED = "mixed"


class Role(StrEnum):
    VIEWER = "viewer"
    EDITOR = "editor"
    ADMIN = "admin"


class LifecycleStatus(StrEnum):
    DRAFT = "draft"
    FINAL = "final"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class Actor:
    user_id: UserId
    email: str
    role: Role


@dataclass(frozen=True, slots=True)
class ImportGrowthRun:
    actor: Actor
    source_name: str
    source_sha256: str
    parser_version: str
    experiment_name: str
    plate_name: str
    experiment_date: date
    fallback_interval_minutes: float = 10.0
    t0_offset_minutes: float = 0.0
    idempotency_key: str = ""


@dataclass(frozen=True, slots=True)
class GrowthRunMetadata:
    """Rich run metadata retained by the legacy Growth v4 workflow."""

    project: str | None = None
    tags: tuple[str, ...] = ()
    operator_name: str | None = None
    instrument: str | None = None
    temperature: float | None = None
    temperature_unit: str | None = None
    measurement_type: str | None = None
    manual_subtraction: float = 0.0
    notes: str | None = None
    experiment_custom_json: dict[str, object] = field(default_factory=dict)
    plate_custom_json: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ImportMicPlate:
    actor: Actor
    source_name: str
    source_sha256: str
    parser_version: str
    experiment_name: str
    plate_name: str
    experiment_date: date
    threshold: float
    idempotency_key: str = ""


@dataclass(frozen=True, slots=True)
class MicExperimentMetadata:
    operator_name: str | None = None
    reader: str | None = None
    incubation_time_hours: float | None = None
    inoculum_od: float | None = None
    growth_phase: str | None = None
    harvest_od: float | None = None
    doubling_time_minutes: float | None = None
    notes: str | None = None
    custom_json: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MicWellLayoutChange:
    position: str
    value_raw: float | None = None
    display_name: str | None = None
    is_blank: bool | None = None
    strain: str | None = None
    treatment: str | None = None
    concentration: float | None = None
    concentration_unit: str | None = None
    medium: str | None = None
    replicate: int | None = None
    notes: str | None = None
    custom_labels: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class UpdateMicLayout:
    actor: Actor
    plate_id: PlateId
    expected_updated_at: str
    changes: tuple[MicWellLayoutChange, ...]


@dataclass(frozen=True, slots=True)
class UpdateMicMetadata:
    actor: Actor
    plate_id: PlateId
    expected_updated_at: str
    experiment_name: str | None = None
    plate_name: str | None = None
    project: str | None = None
    experiment_date: date | None = None
    tags: tuple[str, ...] | None = None
    operator_name: str | None = None
    reader: str | None = None
    incubation_time_hours: float | None = None
    inoculum_od: float | None = None
    growth_phase: str | None = None
    harvest_od: float | None = None
    doubling_time_minutes: float | None = None
    instrument: str | None = None
    notes: str | None = None
    threshold: float | None = None
    experiment_custom_json: dict[str, object] | None = None
    plate_custom_json: dict[str, object] | None = None
    lifecycle_status: LifecycleStatus | None = None


@dataclass(frozen=True, slots=True)
class SetMicReviewState:
    actor: Actor
    plate_id: PlateId
    expected_updated_at: str
    checked: bool


@dataclass(frozen=True, slots=True)
class SetMicLockState:
    actor: Actor
    plate_id: PlateId
    expected_updated_at: str
    locked: bool


@dataclass(frozen=True, slots=True)
class SoftDeleteMicPlate:
    actor: Actor
    plate_id: PlateId
    expected_updated_at: str


@dataclass(frozen=True, slots=True)
class SearchMicResults:
    actor: Actor
    strain: str | None = None
    treatment: str | None = None
    medium: str | None = None
    text: str = ""
    include_deleted: bool = False
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class UpdatePlateMetadata:
    actor: Actor
    plate_id: PlateId
    expected_updated_at: str
    experiment_name: str | None = None
    plate_name: str | None = None
    project: str | None = None
    experiment_date: date | None = None
    tags: tuple[str, ...] | None = None
    operator_name: str | None = None
    instrument: str | None = None
    channel: str | None = None
    temperature: float | None = None
    temperature_unit: str | None = None
    measurement_type: str | None = None
    manual_subtraction: float | None = None
    notes: str | None = None
    experiment_custom_json: dict[str, object] | None = None
    plate_custom_json: dict[str, object] | None = None
    lifecycle_status: LifecycleStatus | None = None


@dataclass(frozen=True, slots=True)
class UpdateWellLayout:
    actor: Actor
    plate_id: PlateId
    expected_updated_at: str
    changes: tuple[WellLayoutChange, ...]


@dataclass(frozen=True, slots=True)
class WellLayoutChange:
    position: str
    display_name: str | None = None
    is_blank: bool | None = None
    background_group: str | None = None
    strain: str | None = None
    medium: str | None = None
    treatment: str | None = None
    concentration: float | None = None
    concentration_unit: str | None = None
    replicate: int | None = None
    plot_selected: bool | None = None
    notes: str | None = None
    grouping_label: str | None = None
    inoculum_size: float | None = None
    inoculum_unit: str | None = None
    custom_fields: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ComputeGrowthBackgroundRevision:
    actor: Actor
    plate_id: PlateId
    algorithm_version: str
    parameters: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ComputeMicRevision:
    actor: Actor
    plate_id: PlateId
    algorithm_version: str
    threshold: float
    parameters: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SearchRuns:
    actor: Actor
    text: str = ""
    assay_type: AssayType | None = None
    project: str | None = None
    strain: str | None = None
    medium: str | None = None
    treatment: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    include_deleted: bool = False
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class ExportPortableRun:
    actor: Actor
    plate_ids: tuple[PlateId, ...]
    revision_ids: tuple[RevisionId, ...] = ()


@dataclass(frozen=True, slots=True)
class ImportPortableRun:
    actor: Actor
    archive_sha256: str
    collision_policy: str = "remap"
    dry_run: bool = True


ALGORITHM_VERSIONS = {
    "growth_normalization": "growth-normalize/1.0.0",
    "growth_background": "growth-background/1.0.0",
    "mic_endpoint": "mic-endpoint/1.0.0",
}
