"""Metadata-only UI for discovering conditions shared across Growth plates."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import cast

import pandas as pd
import streamlit as st

from plate_reader.application.contracts import PlateId
from plate_reader.application.services.growth_comparison import (
    DEFAULT_GROWTH_COMPARISON_FIELDS,
    FindCommonGrowthConditionsService,
    GrowthComparisonMatchField,
    GrowthComparisonPlate,
    GrowthComparisonPlotResult,
    GrowthComparisonResult,
    GrowthConditionKey,
    LoadGrowthComparisonConditionsService,
    LoadGrowthComparisonPlotService,
)
from plate_reader.ui.context import AppContext
from plate_reader.ui.plotting import GrowthPlotOptions, growth_curve_figure, plot_download_config


def render_growth_comparison(context: AppContext) -> None:
    """Render condition discovery without loading any raw Growth measurements."""

    st.header("Plate Comparison")
    plate_ids = _comparison_plate_ids(st.session_state.get("growth_comparison_plate_ids", ()))
    if len(plate_ids) < 2:
        st.info("Select at least two runs in the Growth Run Library to compare them.")
        return
    if len(set(plate_ids)) != len(plate_ids):
        st.error("The selected runs must have unique plate IDs.")
        return

    try:
        plates = _load_comparison_plates(context, plate_ids)
    except Exception as error:
        st.error(f"Unable to load selected run conditions: {error}")
        return

    plate_id_key = tuple(str(plate_id) for plate_id in plate_ids)
    _clear_stale_comparison_state(plate_id_key)
    st.subheader("Selected runs")
    for plate in plates:
        st.caption(_comparison_plate_label(plate))

    with st.form("growth-comparison-match-fields"):
        fields = st.multiselect(
            "Match fields",
            tuple(GrowthComparisonMatchField),
            default=DEFAULT_GROWTH_COMPARISON_FIELDS,
            format_func=_match_field_label,
            help="Concentration always includes its unit. Add medium when it must also match.",
        )
        find_matches = st.form_submit_button("Find common settings", type="primary")

    if find_matches:
        _clear_comparison_match_state()
        if not fields:
            st.error("Choose at least one match field before finding common settings.")
            return
        result = FindCommonGrowthConditionsService().execute(plates, fields)
        st.session_state.growth_comparison_result = result
        st.session_state.growth_comparison_result_plate_ids = plate_id_key
        st.session_state.growth_comparison_table_revision = (
            int(st.session_state.get("growth_comparison_table_revision", 0)) + 1
        )

    stored_result = _stored_comparison_result(plate_id_key)
    if stored_result is None:
        st.info("Choose the settings that must match, then find common settings.")
        return

    _render_exclusions(stored_result, plates)
    if not stored_result.matches:
        st.info("No common settings were found for the selected match fields.")
        return

    st.subheader("Common settings")
    table, condition_keys = _comparison_match_table(stored_result)
    revision = int(st.session_state.get("growth_comparison_table_revision", 0))
    with st.form("growth-comparison-condition-selection"):
        edited_table = st.data_editor(
            table,
            key=f"growth-comparison-condition-table-{revision}",
            hide_index=True,
            width="stretch",
            disabled=[column for column in table.columns if column != "Select"],
            column_config={
                "Select": st.column_config.CheckboxColumn("Select", default=False),
            },
        )
        render_curves = st.form_submit_button("Render comparison curves", type="primary")

    if render_curves:
        selected_keys = _selected_condition_keys(edited_table, condition_keys)
        if not selected_keys:
            st.error("Select at least one common setting before rendering comparison curves.")
            return
        selected_key_set = set(selected_keys)
        selected_matches = tuple(
            match for match in stored_result.matches if match.condition in selected_key_set
        )
        try:
            plot_result = LoadGrowthComparisonPlotService(context.repository).execute(
                context.actor, plates, selected_matches
            )
        except Exception as error:
            st.error(f"Unable to render comparison curves: {error}")
            return
        st.session_state.growth_comparison_selected_condition_keys = selected_keys
        st.session_state.growth_comparison_selected_result = stored_result
        st.session_state.growth_comparison_plot_result = plot_result
        st.session_state.growth_comparison_plot_plate_ids = plate_id_key

    _render_comparison_plot(plate_id_key)


def _comparison_plate_ids(values: object) -> tuple[PlateId, ...]:
    if not isinstance(values, tuple):
        return ()
    return tuple(PlateId(str(value).strip()) for value in values if str(value).strip())


def _load_comparison_plates(
    context: AppContext, plate_ids: tuple[PlateId, ...]
) -> tuple[GrowthComparisonPlate, ...]:
    """Cache the one condition-only loader call by its exact selected plate order."""

    cache = cast(
        dict[tuple[str, ...], tuple[GrowthComparisonPlate, ...]],
        st.session_state.setdefault("growth_comparison_condition_cache", {}),
    )
    cache_key = tuple(str(plate_id) for plate_id in plate_ids)
    if cache_key not in cache:
        cache[cache_key] = LoadGrowthComparisonConditionsService(context.repository).execute(
            context.actor, plate_ids
        )
    return cache[cache_key]


def _clear_stale_comparison_state(plate_ids: tuple[str, ...]) -> None:
    stored_ids = st.session_state.get("growth_comparison_result_plate_ids")
    if stored_ids is not None and stored_ids != plate_ids:
        _clear_comparison_match_state()


def _clear_comparison_match_state() -> None:
    for key in (
        "growth_comparison_result",
        "growth_comparison_result_plate_ids",
        "growth_comparison_selected_condition_keys",
        "growth_comparison_selected_result",
        "growth_comparison_plot_result",
        "growth_comparison_plot_plate_ids",
    ):
        st.session_state.pop(key, None)


def _stored_comparison_result(plate_ids: tuple[str, ...]) -> GrowthComparisonResult | None:
    result = st.session_state.get("growth_comparison_result")
    if (
        isinstance(result, GrowthComparisonResult)
        and st.session_state.get("growth_comparison_result_plate_ids") == plate_ids
    ):
        return result
    return None


def _comparison_plate_label(plate: GrowthComparisonPlate) -> str:
    experiment = plate.experiment_name or "Unnamed experiment"
    plate_name = plate.plate_name or "Unnamed plate"
    return f"{experiment} — {plate_name} ({plate.plate_id})"


def _match_field_label(field: GrowthComparisonMatchField) -> str:
    return field.value.replace("_", " ").capitalize()


def _render_exclusions(
    result: GrowthComparisonResult, plates: Sequence[GrowthComparisonPlate]
) -> None:
    labels_by_id = {plate.plate_id: _comparison_plate_label(plate) for plate in plates}
    st.caption("Excluded wells (blank or missing a selected match field)")
    for exclusion in result.exclusions:
        st.caption(
            f"{labels_by_id.get(exclusion.plate_id, exclusion.plate_id)}: "
            f"{exclusion.blank_well_count} blank, "
            f"{exclusion.missing_required_metadata_count} missing metadata"
        )


def _comparison_match_table(
    result: GrowthComparisonResult,
) -> tuple[pd.DataFrame, dict[str, GrowthConditionKey]]:
    """Build fixed rows indexed by an opaque but stable normalized condition key."""

    keys: dict[str, GrowthConditionKey] = {}
    rows: list[dict[str, str | bool]] = []
    for match in result.matches:
        identifier = _condition_identifier(match.condition)
        keys[identifier] = match.condition
        concentration = "—"
        if match.display.concentration is not None:
            concentration = " ".join(
                value
                for value in (match.display.concentration, match.display.concentration_unit)
                if value is not None
            )
        rows.append(
            {
                "condition_identifier": identifier,
                "Select": False,
                "Strain": match.display.strain or "—",
                "Treatment": match.display.treatment or "—",
                "Concentration": concentration,
                "Medium": match.display.medium or "—",
                "Wells per plate": ", ".join(
                    f"{plate_match.plate_id}: {len(plate_match.wells)}"
                    for plate_match in match.plate_matches
                ),
            }
        )
    return pd.DataFrame.from_records(rows).set_index("condition_identifier"), keys


def _condition_identifier(condition: GrowthConditionKey) -> str:
    """Serialize every normalized key field so different conditions cannot collide."""

    return json.dumps(
        (
            condition.strain,
            condition.treatment,
            str(condition.concentration) if condition.concentration is not None else None,
            condition.concentration_unit,
            condition.medium,
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _selected_condition_keys(
    table: pd.DataFrame, condition_keys: dict[str, GrowthConditionKey]
) -> tuple[GrowthConditionKey, ...]:
    """Recover selected normalized keys from the submitted, stable table index."""

    return tuple(condition_keys[str(identifier)] for identifier in table.index[table["Select"]])


def _render_comparison_plot(plate_ids: tuple[str, ...]) -> None:
    value = st.session_state.get("growth_comparison_plot_result")
    if not isinstance(value, GrowthComparisonPlotResult):
        return
    if st.session_state.get("growth_comparison_plot_plate_ids") != plate_ids:
        return
    options = GrowthPlotOptions(
        title="Plate comparison",
        dark_mode=bool(st.session_state.get("dark_mode", False)),
    )
    figure = growth_curve_figure(
        value.plot_data,
        options,
        value.cache_key,
        "raw",
    )
    st.subheader("Comparison curves")
    st.caption(f"{value.plate_count} plates · {value.well_count} wells · raw values")
    for issue in value.plot_data.issues:
        st.warning(issue.message)
    st.plotly_chart(
        figure,
        width="stretch",
        config=plot_download_config(options.title, "plate-comparison"),
    )
