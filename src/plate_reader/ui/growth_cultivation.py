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
)
from plate_reader.application.services.growth_workflow import GrowthRunView
from plate_reader.ui.context import AppContext

_DESCRIPTION_FIELDS = (
    ("CultivationExperiment", "Cultivation experiment ID"),
    ("InoculationDateTime", "Inoculation date/time (YYYY-MM-DD HH:MM)"),
    ("ProgramMetric", "Program metric"),
    ("EquipmentMakeModel", "Equipment make/model"),
    ("Objective", "Objective"),
    ("CultivationProtocol", "Cultivation protocol"),
    ("SampleAnalysisProtocol", "Sample analysis protocol"),
    ("Comment", "Cultivation comment"),
)


def render_growth_cultivations(context: AppContext, plate_id: PlateId, view: GrowthRunView) -> bool:
    """Return true only after an explicit, successful atomic registry save."""

    with st.expander("Cultivation metadata and ID generator", expanded=False):
        st.markdown(
            "IDs follow **Team-EXP-Strain-SystemNNNRreplicate**, for example "
            "`PN-EXP-11_J3-BRV002R1`. Enter your assigned cultivation run number manually; "
            "it is separate from the experiment date. Strain and biological replicate come "
            "from the saved Layout. Confirm that Layout Replicate represents biological replicates."
        )
        st.caption(
            "Select the wells to generate or regenerate. Unselected wells keep their saved IDs. "
            "You can save descriptive metadata with all wells unselected. Per-well custom "
            "columns with the registry field names can override descriptive defaults."
        )
        snapshot = view.snapshot
        shared = json_object(
            json_object(snapshot.metadata.get("plate_custom_json") or {}).get(
                "cultivation_registry", {}
            )
        )
        rows: list[dict[str, object]] = []
        for well in snapshot.wells:
            custom = json_object(well.get("custom_json") or {})
            rows.append(
                {
                    "Generate": False,
                    "Well": str(well["position"]),
                    "Strain": str(well.get("strain") or ""),
                    "Biological replicate": well.get("replicate"),
                    "Cultivation run number": str(custom.get("CultivationRun") or ""),
                    "Saved cultivation ID": str(custom.get("Cultivation") or ""),
                }
            )
        version = str(snapshot.metadata.get("updated_at", ""))
        with st.form(f"cultivation-form-{plate_id}-{version}"):
            registry = dict(shared)
            left, right = st.columns(2)
            registry["Team_Code"] = left.text_input(
                "Team code", value=str(shared.get("Team_Code") or "")
            )
            registry["CultivationSystemCode"] = right.text_input(
                "Cultivation system / experiment code",
                value=str(shared.get("CultivationSystemCode") or ""),
                help="For example MP96A or BRV. No run number or replicate suffix.",
            )
            for field, label in _DESCRIPTION_FIELDS:
                registry[field] = st.text_input(label, value=str(shared.get(field) or ""))
            edited = st.data_editor(
                pd.DataFrame(rows),
                hide_index=True,
                width="stretch",
                key=f"cultivation-wells-{plate_id}-{version}",
                disabled=["Well", "Strain", "Biological replicate", "Saved cultivation ID"],
                column_config={
                    "Generate": st.column_config.CheckboxColumn("Generate", default=False),
                    "Cultivation run number": st.column_config.TextColumn(
                        "Cultivation run number", help="Manual positive number, e.g. 23 → 023."
                    ),
                },
            )
            preview = st.form_submit_button("Preview cultivation IDs")
            save = st.form_submit_button("Save cultivation metadata and IDs")
        if preview or save:
            try:
                assignments = tuple(
                    CultivationAssignment(str(row["Well"]), str(row["Cultivation run number"]))
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
