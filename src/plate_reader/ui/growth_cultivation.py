"""Explicit cultivation registry metadata and identity generation controls."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from plate_reader.application.contracts import PlateId
from plate_reader.application.services.growth_cultivation import (
    CultivationAssignment,
    SaveGrowthCultivationsService,
    json_object,
    preview_cultivations,
    suggested_cultivation_experiment_code,
)
from plate_reader.application.services.growth_workflow import GrowthRunView
from plate_reader.domain.growth.cultivation import (
    DEFAULT_CULTIVATION_PATTERN,
    LEGACY_CULTIVATION_PATTERN,
)
from plate_reader.ui.context import AppContext

CULTIVATION_DESCRIPTION_FIELDS = (
    ("CultivationExperiment", "Cultivation experiment ID"),
    ("InoculationDateTime", "Inoculation date/time (YYYY-MM-DD HH:MM)"),
    ("ProgramMetric", "Program metric"),
    ("EquipmentMakeModel", "Equipment make/model"),
    ("Objective", "Objective"),
    ("CultivationProtocol", "Cultivation protocol"),
    ("SampleAnalysisProtocol", "Sample analysis protocol"),
    ("Comment", "Cultivation comment"),
)


_AUTOMATIC = "Experiment number + well (recommended)"
_LABORATORY = "Original laboratory format (manual number)"
_CUSTOM = "Custom pattern"


def render_growth_cultivations(context: AppContext, plate_id: PlateId, view: GrowthRunView) -> bool:
    """Apply a chosen naming pattern to each selected well on explicit save."""

    with st.expander("Cultivation metadata and ID pattern", expanded=False):
        st.markdown(
            "**Each well has its own cultivation ID.** A run may contain multiple strains, "
            "conditions and biological replicates. The pattern uses each well's saved "
            "**Strain** and **Replicate** from Layout."
        )
        st.caption(
            "Experiment numbers start at 001. Dates remain in metadata. "
            "Well position keeps IDs distinct even when replicate labels repeat across conditions. "
            "Set biological replicate values in Layout; they are not inferred from well positions."
        )
        snapshot = view.snapshot
        shared = json_object(
            json_object(snapshot.metadata.get("plate_custom_json") or {}).get(
                "cultivation_registry", {}
            )
        )
        version = str(snapshot.metadata.get("updated_at", ""))
        saved_pattern = str(shared.get("CultivationIDPattern") or DEFAULT_CULTIVATION_PATTERN)
        options = (_AUTOMATIC, _LABORATORY, _CUSTOM)
        initial = (
            _AUTOMATIC
            if saved_pattern == DEFAULT_CULTIVATION_PATTERN
            else _LABORATORY
            if saved_pattern == LEGACY_CULTIVATION_PATTERN
            else _CUSTOM
        )
        choice = st.selectbox(
            "Cultivation ID pattern",
            options,
            index=options.index(initial),
            key=f"cultivation-pattern-choice-{plate_id}-{version}",
        )
        select_samples = st.checkbox(
            "Select all sample wells with a strain",
            value=True,
            key=f"cultivation-select-samples-{plate_id}-{version}",
        )
        rows: list[dict[str, object]] = []
        for well in snapshot.wells:
            custom = json_object(well.get("custom_json") or {})
            row: dict[str, object] = {
                "Generate": bool(
                    select_samples and well.get("strain") and not well.get("is_blank")
                ),
                "Well": str(well["position"]),
                "Strain": str(well.get("strain") or ""),
                "Biological replicate": well.get("replicate"),
                "Saved cultivation ID": str(custom.get("Cultivation") or ""),
            }
            if choice != _AUTOMATIC:
                row["Cultivation run number"] = str(custom.get("CultivationRun") or "")
            rows.append(row)
        with st.form(f"cultivation-form-{plate_id}-{version}-{choice}"):
            registry = dict(shared)
            pattern = (
                DEFAULT_CULTIVATION_PATTERN if choice == _AUTOMATIC else LEGACY_CULTIVATION_PATTERN
            )
            if choice == _CUSTOM:
                pattern = st.text_input(
                    "Custom cultivation ID pattern",
                    value=saved_pattern,
                    help=(
                        "Tokens: {team}, {strain}, {system}, {run}, "
                        "{experiment}, {well}, {replicate}."
                    ),
                )
            else:
                st.code(pattern, language=None)
            registry["CultivationIDPattern"] = pattern
            experiment_code = str(shared.get("CultivationExperimentCode") or "")
            if choice != _LABORATORY:
                experiment_code = experiment_code or suggested_cultivation_experiment_code(
                    context.repository
                )
                experiment_code = st.text_input(
                    "Experiment number",
                    value=experiment_code,
                    help=(
                        "The next available number is suggested automatically (001, 002, …). "
                        "It is saved with this run; you can edit it."
                    ),
                )
                st.caption(
                    "The number is reserved when you save. Previewing does not use up a number."
                )
            if choice == _LABORATORY:
                registry.pop("CultivationExperimentCode", None)
            else:
                registry["CultivationExperimentCode"] = experiment_code
            left, right = st.columns(2)
            registry["Team_Code"] = left.text_input(
                "Team code", value=str(shared.get("Team_Code") or "")
            )
            registry["CultivationSystemCode"] = right.text_input(
                "Cultivation system / experiment code",
                value=str(shared.get("CultivationSystemCode") or ""),
                help=(
                    "Optional for the recommended pattern. "
                    "Used by the original laboratory format or {system}."
                ),
            )
            for field, label in CULTIVATION_DESCRIPTION_FIELDS:
                registry[field] = st.text_input(label, value=str(shared.get(field) or ""))
            edited = st.data_editor(
                pd.DataFrame(rows),
                hide_index=True,
                width="stretch",
                key=f"cultivation-wells-{plate_id}-{version}-{choice}-{select_samples}",
                disabled=["Well", "Strain", "Biological replicate", "Saved cultivation ID"],
                column_config={
                    "Generate": st.column_config.CheckboxColumn("Apply pattern"),
                    "Cultivation run number": st.column_config.TextColumn(
                        "Cultivation run number",
                        help="For {run} only: a manual number, e.g. 23 → 023.",
                    ),
                },
            )
            preview = st.form_submit_button("Preview cultivation IDs")
            save = st.form_submit_button("Save cultivation metadata and IDs")
        if preview or save:
            try:
                assignments = tuple(
                    CultivationAssignment(
                        str(row["Well"]),
                        str(row.get("Cultivation run number") or ""),
                        pattern=None if choice == _LABORATORY else pattern,
                        experiment_code=None if choice == _LABORATORY else experiment_code,
                    )
                    for row in edited.to_dict(orient="records")
                    if row["Generate"]
                )
                generated = preview_cultivations(snapshot, registry, assignments)
                if generated:
                    st.dataframe(pd.DataFrame(generated), hide_index=True, width="stretch")
                else:
                    st.info("No wells selected. Only descriptive metadata will be saved.")
                if save:
                    SaveGrowthCultivationsService(context.repository).execute(
                        context.actor, plate_id, version, registry, assignments
                    )
                    return True
            except Exception as error:
                st.error(f"Unable to prepare or save cultivation metadata: {error}")
    return False
