"""Staged, metadata-first selection of individual Growth wells for comparison."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal, InvalidOperation
from numbers import Real
from typing import cast

import pandas as pd
import streamlit as st

from plate_reader.application.contracts import PlateId
from plate_reader.application.services.growth_comparison import (
    GrowthComparisonPlate,
    GrowthComparisonPlotResult,
    GrowthComparisonWell,
    GrowthWellSearchFilter,
    GrowthWellSearchResult,
    LoadGrowthComparisonPlotService,
    LoadGrowthComparisonWellIndexService,
    SearchGrowthComparisonWellsService,
    growth_comparison_summary_fields,
)
from plate_reader.ui.context import AppContext
from plate_reader.ui.plotting import GrowthPlotOptions, growth_curve_figure, plot_download_config


def render_growth_comparison(context: AppContext) -> None:
    """Render an explicit, persistent well-selection workflow.

    The plate index is metadata-only.  Form controls deliberately keep browser-side
    edits local until Search, Add, Remove, Clear, or Render is submitted.
    """

    st.header("Plate Comparison")
    plate_ids = _comparison_plate_ids(st.session_state.get("growth_comparison_plate_ids", ()))
    if len(plate_ids) < 2:
        st.info("Select at least two runs in the Growth Run Library to compare them.")
        return
    if len(set(plate_ids)) != len(plate_ids):
        st.error("The selected runs must have unique plate IDs.")
        return

    source_key = tuple(str(plate_id) for plate_id in plate_ids)
    _clear_for_changed_sources(source_key)
    try:
        plate_index = _load_plate_index(context, plate_ids)
    except Exception as error:
        st.error(f"Unable to load selected run metadata: {error}")
        return

    labels = {plate.plate_id: _comparison_plate_label(plate) for plate in plate_index}
    st.subheader("Selected runs")
    for plate in plate_index:
        st.caption(_comparison_plate_label(plate))

    _render_well_search(plate_index, labels, source_key)
    _render_search_results(plate_index, source_key)
    _render_selection_basket(context, plate_index, source_key)
    _render_comparison_plot(source_key)


def _comparison_plate_ids(values: object) -> tuple[PlateId, ...]:
    """Accept only the tuple that Library owns in session state."""

    if not isinstance(values, tuple):
        return ()
    return tuple(PlateId(str(value).strip()) for value in values if str(value).strip())


def _load_plate_index(
    context: AppContext, plate_ids: tuple[PlateId, ...]
) -> tuple[GrowthComparisonPlate, ...]:
    """Cache the one metadata-only index lookup by exact source run order."""

    cache = cast(
        dict[tuple[str, ...], tuple[GrowthComparisonPlate, ...]],
        st.session_state.setdefault("growth_comparison_plate_index_cache", {}),
    )
    cache_key = tuple(str(plate_id) for plate_id in plate_ids)
    if cache_key not in cache:
        cache[cache_key] = LoadGrowthComparisonWellIndexService(context.repository).execute(
            context.actor, plate_ids
        )
    return cache[cache_key]


def _clear_for_changed_sources(source_key: tuple[str, ...]) -> None:
    """Clear selections when Library hands us a different source-run set."""

    previous = st.session_state.get("growth_comparison_source_plate_ids")
    if previous is not None and previous != source_key:
        # The index can be nearly 10,000 metadata rows.  It is cheap to recreate
        # for the new explicit source set and should not accumulate across runs.
        st.session_state.pop("growth_comparison_plate_index_cache", None)
        for key in (
            "growth_comparison_search_result",
            "growth_comparison_search_source_plate_ids",
            "growth_comparison_basket",
            "growth_comparison_plot_result",
            "growth_comparison_plot_source_plate_ids",
            "growth_comparison_plot_basket_keys",
        ):
            st.session_state.pop(key, None)
        st.session_state.growth_comparison_search_revision = (
            int(st.session_state.get("growth_comparison_search_revision", 0)) + 1
        )
        st.session_state.growth_comparison_basket_revision = (
            int(st.session_state.get("growth_comparison_basket_revision", 0)) + 1
        )
    st.session_state.growth_comparison_source_plate_ids = source_key


def _render_well_search(
    plate_index: Sequence[GrowthComparisonPlate],
    labels: dict[str, str],
    source_key: tuple[str, ...],
) -> None:
    st.subheader("Find wells")
    selectable_plate_ids = tuple(plate.plate_id for plate in plate_index)
    wells = _index_wells(plate_index)
    summary_fields = growth_comparison_summary_fields()
    summary_fields_by_label = {field.label: field.key for field in summary_fields}
    with st.form("growth-comparison-well-search"):
        selected_sources = st.multiselect(
            "Source runs",
            selectable_plate_ids,
            default=selectable_plate_ids,
            format_func=lambda plate_id: labels[plate_id],
            help="Limit this search to one or more of the runs selected in Library.",
        )
        text = st.text_input(
            "Well or metadata contains",
            help=(
                "Search well position, display name, strain, treatment, concentration unit, "
                "medium, group, or inoculum unit."
            ),
        )
        left, middle, right = st.columns(3)
        with left:
            strains = st.multiselect("Strain", _facet_values(wells, "strain"))
            treatments = st.multiselect("Treatment", _facet_values(wells, "treatment"))
            media = st.multiselect("Medium", _facet_values(wells, "medium"))
            grouping_labels = st.multiselect("Group", _facet_values(wells, "grouping_label"))
        with middle:
            concentration_min = st.text_input("Minimum concentration")
            concentration_max = st.text_input("Maximum concentration")
            concentration_units = st.multiselect(
                "Concentration unit", _facet_values(wells, "concentration_unit")
            )
        with right:
            inoculum_sizes = st.multiselect(
                "Inoculum size",
                _facet_values(wells, "inoculum_size"),
                format_func=_facet_label,
            )
            inoculum_units = st.multiselect("Inoculum unit", _facet_values(wells, "inoculum_unit"))
            replicates = st.multiselect("Replicate", _facet_values(wells, "replicate"))
            include_blank_wells = st.checkbox("Include blank wells", value=False)
        quick_stat_labels = st.multiselect(
            "Quick stats group by",
            tuple(summary_fields_by_label),
            default=("Strain", "Treatment", "Concentration", "Medium", "Inoculum size"),
            help=(
                "Counts wells sharing the same selected metadata. The saved Replicate value "
                "is deliberately not used."
            ),
        )
        search = st.form_submit_button("Search wells", type="primary")

    if not search:
        return
    try:
        if not selected_sources:
            raise ValueError("Choose at least one source run before searching")
        minimum = _optional_decimal(concentration_min, "Minimum concentration")
        maximum = _optional_decimal(concentration_max, "Maximum concentration")
        if (minimum is not None or maximum is not None) and len(concentration_units) != 1:
            raise ValueError(
                "Choose exactly one concentration unit when using a concentration range"
            )
        search_filter = GrowthWellSearchFilter(
            source_plate_ids=tuple(selected_sources),
            text=text,
            strains=_text_selection(strains),
            treatments=_text_selection(treatments),
            concentration_min=minimum,
            concentration_max=maximum,
            concentration_units=_text_selection(concentration_units),
            media=_text_selection(media),
            replicates=_replicate_selection(replicates),
            grouping_labels=_text_selection(grouping_labels),
            inoculum_sizes=_numeric_selection(inoculum_sizes),
            inoculum_units=_text_selection(inoculum_units),
            include_blank_wells=include_blank_wells,
        )
        result = SearchGrowthComparisonWellsService().execute(
            plate_index,
            search_filter,
            tuple(summary_fields_by_label[label] for label in quick_stat_labels),
        )
    except Exception as error:
        st.error(f"Unable to search wells: {error}")
        return
    st.session_state.growth_comparison_search_result = result
    st.session_state.growth_comparison_search_source_plate_ids = source_key
    st.session_state.growth_comparison_search_revision = (
        int(st.session_state.get("growth_comparison_search_revision", 0)) + 1
    )


def _optional_decimal(value: str, label: str) -> Decimal | None:
    text = value.strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"{label} must be a number") from error


def _text_selection(values: Iterable[object]) -> tuple[str, ...]:
    return tuple(str(value) for value in values)


def _replicate_selection(values: Iterable[object]) -> tuple[int, ...]:
    values_tuple = tuple(values)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values_tuple):
        raise ValueError("Replicate selections must be positive integers")
    return cast(tuple[int, ...], values_tuple)


def _numeric_selection(values: Iterable[object]) -> tuple[int | float | Decimal, ...]:
    values_tuple = tuple(values)
    if any(
        isinstance(value, bool) or not isinstance(value, Real | Decimal) for value in values_tuple
    ):
        raise ValueError("Inoculum-size selections must be numeric")
    return cast(tuple[int | float | Decimal, ...], values_tuple)


def _render_search_results(
    plate_index: Sequence[GrowthComparisonPlate], source_key: tuple[str, ...]
) -> None:
    result = _stored_search_result(source_key)
    if result is None:
        st.info("Choose filters and press Search wells. No measurements are loaded during search.")
        return
    if not result.wells:
        st.info("No wells match the submitted filters.")
        return

    st.subheader("Matching wells")
    if result.truncated:
        st.warning(f"Showing the first {len(result.wells)} of {result.total} matching wells.")
    _render_quick_stats(result)
    table, wells_by_key = _well_table(result.wells, plate_index)
    revision = int(st.session_state.get("growth_comparison_search_revision", 0))
    with st.form("growth-comparison-add-wells"):
        edited = st.data_editor(
            table,
            key=f"growth-comparison-search-table-{revision}",
            hide_index=True,
            width="stretch",
            disabled=[column for column in table.columns if column != "Select"],
            column_config={"Select": st.column_config.CheckboxColumn("Select", default=False)},
        )
        add_left, add_right = st.columns(2)
        add_selected = add_left.form_submit_button("Add selected wells", type="primary")
        add_all = add_right.form_submit_button("Add all displayed")

    if not add_selected and not add_all:
        return
    selected = tuple(result.wells) if add_all else _selected_wells(edited, wells_by_key)
    if not selected:
        st.error("Select at least one well to add.")
        return
    _add_to_basket(selected)


def _render_quick_stats(result: GrowthWellSearchResult) -> None:
    quick_stats = result.quick_stats
    if quick_stats is None:
        return
    st.subheader("Quick stats")
    largest_group = max((group.well_count for group in quick_stats.groups), default=0)
    metrics = st.columns(3)
    metrics[0].metric("Matching wells", quick_stats.total_wells)
    metrics[1].metric("Condition groups", len(quick_stats.groups))
    metrics[2].metric("Largest well group", largest_group)
    st.caption(
        "Replicate wells (n) is the actual number of wells sharing the selected metadata "
        "combination; the saved Replicate field is not used."
    )
    records = [
        {
            **dict(zip((field.label for field in quick_stats.fields), group.values, strict=True)),
            "Replicate wells (n)": group.well_count,
            "Runs represented": group.plate_count,
        }
        for group in quick_stats.groups
    ]
    st.dataframe(pd.DataFrame.from_records(records), hide_index=True, width="stretch", height=280)


def _stored_search_result(source_key: tuple[str, ...]) -> GrowthWellSearchResult | None:
    result = st.session_state.get("growth_comparison_search_result")
    if (
        isinstance(result, GrowthWellSearchResult)
        and st.session_state.get("growth_comparison_search_source_plate_ids") == source_key
    ):
        return result
    return None


def _render_selection_basket(
    context: AppContext,
    plate_index: Sequence[GrowthComparisonPlate],
    source_key: tuple[str, ...],
) -> None:
    st.subheader("Plot selection")
    basket = _basket_wells()
    if not basket:
        st.info("Add wells from one or more searches to build a comparison plot.")
        return

    table, wells_by_key = _well_table(basket, plate_index, select_label="Remove")
    revision = int(st.session_state.get("growth_comparison_basket_revision", 0))
    with st.form("growth-comparison-basket-actions"):
        edited = st.data_editor(
            table,
            key=f"growth-comparison-basket-table-{revision}",
            hide_index=True,
            width="stretch",
            disabled=[column for column in table.columns if column != "Remove"],
            column_config={"Remove": st.column_config.CheckboxColumn("Remove", default=False)},
        )
        remove_column, clear_column, render_column = st.columns(3)
        remove_selected = remove_column.form_submit_button("Remove selected")
        clear = clear_column.form_submit_button("Clear selection")
        render = render_column.form_submit_button("Render comparison curves", type="primary")

    if remove_selected:
        selected = _selected_wells(edited, wells_by_key, column="Remove")
        if not selected:
            st.error("Select at least one well to remove.")
            return
        selected_keys = {_well_key(well) for well in selected}
        _set_basket(well for well in basket if _well_key(well) not in selected_keys)
        return
    if clear:
        _set_basket(())
        return
    if not render:
        return

    represented_plate_ids = {well.plate_id for well in basket}
    if len(represented_plate_ids) < 2:
        st.error("Add wells from at least two plates before rendering comparison curves.")
        return
    try:
        result = LoadGrowthComparisonPlotService(context.repository).execute(
            context.actor, tuple(plate_index), basket
        )
    except Exception as error:
        st.error(f"Unable to render comparison curves: {error}")
        return
    st.session_state.growth_comparison_plot_result = result
    st.session_state.growth_comparison_plot_source_plate_ids = source_key
    st.session_state.growth_comparison_plot_basket_keys = tuple(_well_key(well) for well in basket)


def _well_table(
    wells: Sequence[GrowthComparisonWell],
    plate_index: Sequence[GrowthComparisonPlate],
    *,
    select_label: str = "Select",
) -> tuple[pd.DataFrame, dict[str, GrowthComparisonWell]]:
    """Build a sortable fixed-row table whose hidden index is plate+well identity."""

    by_key: dict[str, GrowthComparisonWell] = {}
    plates_by_id = {plate.plate_id: plate for plate in plate_index}
    rows: list[dict[str, str | bool]] = []
    for well in wells:
        key = _well_key(well)
        plate = plates_by_id.get(well.plate_id)
        by_key[key] = well
        rows.append(
            {
                "well_key": key,
                select_label: False,
                "Experiment": plate.experiment_name if plate and plate.experiment_name else "—",
                "Plate": plate.plate_name if plate and plate.plate_name else well.plate_id,
                "Well": well.position,
                "Display name": well.display_name or "—",
                "Strain": well.strain or "—",
                "Treatment": well.treatment or "—",
                "Concentration": _concentration_label(well),
                "Medium": well.medium or "—",
                "Inoculum": _inoculum_label(well),
                "Replicate": "—" if well.replicate is None else str(well.replicate),
                "Group": well.grouping_label or "—",
            }
        )
    return pd.DataFrame.from_records(rows).set_index("well_key"), by_key


def _selected_wells(
    table: pd.DataFrame,
    wells_by_key: dict[str, GrowthComparisonWell],
    *,
    column: str = "Select",
) -> tuple[GrowthComparisonWell, ...]:
    return tuple(wells_by_key[str(key)] for key in table.index[table[column]])


def _add_to_basket(wells: Iterable[GrowthComparisonWell]) -> None:
    existing = _basket_wells()
    _set_basket((*existing, *wells))


def _basket_wells() -> tuple[GrowthComparisonWell, ...]:
    value = st.session_state.get("growth_comparison_basket", ())
    if not isinstance(value, tuple) or not all(
        isinstance(well, GrowthComparisonWell) for well in value
    ):
        return ()
    return value


def _set_basket(wells: Iterable[GrowthComparisonWell]) -> None:
    """Union wells without reordering prior selections and invalidate an old plot."""

    unique: list[GrowthComparisonWell] = []
    seen: set[str] = set()
    for well in wells:
        key = _well_key(well)
        if key not in seen:
            unique.append(well)
            seen.add(key)
    st.session_state.growth_comparison_basket = tuple(unique)
    st.session_state.growth_comparison_basket_revision = (
        int(st.session_state.get("growth_comparison_basket_revision", 0)) + 1
    )
    for key in (
        "growth_comparison_plot_result",
        "growth_comparison_plot_source_plate_ids",
        "growth_comparison_plot_basket_keys",
    ):
        st.session_state.pop(key, None)


def _well_key(well: GrowthComparisonWell) -> str:
    return f"{well.plate_id}:{well.well_id}"


def _index_wells(plate_index: Sequence[GrowthComparisonPlate]) -> tuple[GrowthComparisonWell, ...]:
    return tuple(well for plate in plate_index for well in plate.wells)


def _facet_values(wells: Iterable[GrowthComparisonWell], name: str) -> tuple[object, ...]:
    values = {getattr(well, name) for well in wells if getattr(well, name) is not None}
    return tuple(sorted(values, key=_facet_sort_key))


def _facet_sort_key(value: object) -> tuple[int, Decimal | str, str]:
    if isinstance(value, Real | Decimal) and not isinstance(value, bool):
        return (0, Decimal(str(value)), str(value))
    text = str(value)
    return (1, text.casefold(), text)


def _facet_label(value: object) -> str:
    if isinstance(value, Real | Decimal) and not isinstance(value, bool):
        return format(Decimal(str(value)).normalize(), "f")
    return str(value)


def _comparison_plate_label(plate: GrowthComparisonPlate) -> str:
    experiment = plate.experiment_name or "Unnamed experiment"
    plate_name = plate.plate_name or "Unnamed plate"
    return f"{experiment} — {plate_name} ({plate.plate_id})"


def _concentration_label(well: GrowthComparisonWell) -> str:
    if well.concentration is None:
        return "—"
    value = str(well.concentration)
    return f"{value} {well.concentration_unit}" if well.concentration_unit else value


def _inoculum_label(well: GrowthComparisonWell) -> str:
    if well.inoculum_size is None:
        return "—"
    value = _facet_label(well.inoculum_size)
    return f"{value} {well.inoculum_unit}" if well.inoculum_unit else value


def _render_comparison_plot(source_key: tuple[str, ...]) -> None:
    value = st.session_state.get("growth_comparison_plot_result")
    if not isinstance(value, GrowthComparisonPlotResult):
        return
    if st.session_state.get("growth_comparison_plot_source_plate_ids") != source_key:
        return
    if st.session_state.get("growth_comparison_plot_basket_keys") != tuple(
        _well_key(well) for well in _basket_wells()
    ):
        return
    options = GrowthPlotOptions(
        title="Plate comparison",
        dark_mode=bool(st.session_state.get("dark_mode", False)),
    )
    figure = growth_curve_figure(value.plot_data, options, value.cache_key, "raw")
    st.subheader("Comparison curves")
    st.caption(f"{value.plate_count} plates · {value.well_count} wells · raw values")
    for issue in value.plot_data.issues:
        st.warning(issue.message)
    st.plotly_chart(
        figure,
        width="stretch",
        config=plot_download_config(options.title, "plate-comparison"),
    )
