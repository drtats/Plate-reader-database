"""Repository protocol shared by local, fake-cloud, and cloud adapters."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from plate_reader.application.contracts import AssayType, ExperimentId, PlateId, RevisionId


@dataclass(frozen=True, slots=True)
class ConcentrationRange:
    """Observed concentration bounds for one normalized concentration unit."""

    minimum: float
    maximum: float
    unit: str | None


@dataclass(frozen=True, slots=True)
class InoculumRange:
    """Observed inoculum-size bounds for one normalized inoculum unit."""

    minimum: float
    maximum: float
    unit: str | None


@dataclass(frozen=True, slots=True)
class RunSummary:
    """Metadata-only projection for a single run-library row.

    The metadata tuples are immutable because this read model may be cached by
    callers.  Repository implementations must not load raw measurements while
    constructing it.
    """

    experiment_id: ExperimentId
    plate_id: PlateId
    experiment_name: str
    plate_name: str
    assay_type: AssayType
    experiment_date: str
    project: str | None
    updated_at: str
    strains: tuple[str, ...] = ()
    treatments: tuple[str, ...] = ()
    concentration_ranges: tuple[ConcentrationRange, ...] = ()
    media: tuple[str, ...] = ()
    inoculum_ranges: tuple[InoculumRange, ...] = ()
    custom_fields: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True, slots=True)
class PlateSnapshot:
    plate_id: PlateId
    metadata: dict[str, object]
    wells: tuple[dict[str, object], ...]
    raw_observations: tuple[dict[str, object], ...]
    revisions: tuple[dict[str, object], ...]


@runtime_checkable
class PlateReaderRepository(Protocol):
    """Minimum atomic persistence surface needed by application services.

    Implementations must roll back the complete context when an exception leaves
    ``transaction``. Iterators must be consumed before the repository is closed.
    """

    def transaction(self) -> AbstractContextManager[None]: ...

    def source_exists(self, idempotency_key: str) -> bool: ...

    def create_experiment(self, values: dict[str, object]) -> ExperimentId: ...

    def create_plate(self, values: dict[str, object]) -> PlateId: ...

    def insert_wells(self, plate_id: PlateId, rows: Sequence[dict[str, object]]) -> None: ...

    def insert_raw_observations(
        self, plate_id: PlateId, rows: Sequence[dict[str, object]]
    ) -> None: ...

    def update_plate_metadata(
        self, plate_id: PlateId, expected_updated_at: str, changes: dict[str, object]
    ) -> str: ...

    def add_analysis_revision(self, values: dict[str, object]) -> RevisionId: ...

    def search_runs(self, filters: dict[str, object]) -> Sequence[RunSummary]: ...

    def growth_comparison_wells(
        self, plate_ids: Sequence[PlateId]
    ) -> tuple[dict[str, object], ...]: ...

    def load_plate(self, plate_id: PlateId) -> PlateSnapshot | None: ...

    def stream_growth_measurements(
        self, plate_id: PlateId, *, chunk_size: int = 5_000
    ) -> Iterator[tuple[dict[str, object], ...]]: ...

    def list_plate_templates(
        self, assay_type: AssayType | None = None
    ) -> tuple[dict[str, object], ...]: ...

    def save_plate_template(self, values: dict[str, object]) -> str: ...

    def delete_plate_template(self, template_id: str, expected_updated_at: str) -> None: ...

    def list_saved_options(
        self, option_type: str | None = None
    ) -> tuple[dict[str, object], ...]: ...

    def save_saved_option(self, values: dict[str, object]) -> bool: ...

    def delete_saved_option(self, option_type: str, value: str) -> None: ...
