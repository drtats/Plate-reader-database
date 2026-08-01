"""Reusable template controls embedded beside the shared plate editor."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from plate_reader.application.contracts import (
    AssayType,
    DeletePlateTemplate,
    Role,
    SavePlateTemplate,
)
from plate_reader.application.services import (
    DeletePlateTemplateService,
    ListPlateTemplatesService,
    SavePlateTemplateService,
)
from plate_reader.ui.context import AppContext
from plate_reader.ui.plate_editor import (
    apply_growth_template,
    apply_mic_template,
    growth_template_layout,
    mic_template_layout,
    replace_editor_frame,
)


def render_plate_template_controls(
    context: AppContext,
    *,
    assay_type: AssayType,
    frame: pd.DataFrame,
    state_key: str,
) -> None:  # pragma: no cover - Streamlit widget composition
    """Apply templates for every role and expose write controls to admins."""

    control_key = f"{state_key}_templates"
    with st.expander("Reusable plate templates"):
        flash = st.session_state.pop(f"{control_key}_flash", None)
        if flash:
            st.success(str(flash))
        try:
            templates = ListPlateTemplatesService(context.repository).execute(
                context.actor, assay_type
            )
        except Exception as error:
            st.error(f"Could not load plate templates: {error}")
            return

        by_id = {template.template_id: template for template in templates}
        selected = None
        if by_id:
            selected_id = st.selectbox(
                "Saved template",
                ("", *by_id),
                format_func=lambda value: (
                    "Choose a template" if not value else by_id[value].template_name
                ),
                key=f"{control_key}_selected",
            )
            selected = by_id.get(selected_id)
        else:
            st.caption("No saved templates for this assay yet.")
        if st.button(
            "Apply selected template",
            disabled=selected is None,
            key=f"{control_key}_apply",
        ):
            try:
                if selected is None:
                    raise ValueError("Choose a template to apply")
                updated = (
                    apply_growth_template(frame, selected.layout)
                    if assay_type is AssayType.GROWTH
                    else apply_mic_template(frame, selected.layout)
                )
                replace_editor_frame(state_key, updated)
                st.session_state[f"{control_key}_flash"] = (
                    f"Applied template: {selected.template_name}. Changes remain staged."
                )
                st.rerun()
            except Exception as error:
                st.error(f"Could not apply plate template: {error}")

        if context.actor.role is not Role.ADMIN:
            st.caption("All users can apply templates. Only administrators can manage them.")
            return

        st.divider()
        template_name = st.text_input(
            "New template name",
            key=f"{control_key}_new_name",
            placeholder="Example: Standard 96-well antibiotic layout",
        )
        if st.button("Save current layout as new template", key=f"{control_key}_save_new"):
            try:
                created = SavePlateTemplateService(context.repository).execute(
                    SavePlateTemplate(
                        context.actor,
                        template_name,
                        assay_type,
                        _template_layout(assay_type, frame),
                    )
                )
                st.session_state[f"{control_key}_flash"] = (
                    f"Saved template: {created.template_name}."
                )
                st.rerun()
            except Exception as error:
                st.error(f"Could not save plate template: {error}")

        overwrite, delete = st.columns(2)
        if overwrite.button(
            "Overwrite selected template",
            disabled=selected is None,
            key=f"{control_key}_overwrite",
        ):
            try:
                if selected is None:
                    raise ValueError("Choose a template to overwrite")
                SavePlateTemplateService(context.repository).execute(
                    SavePlateTemplate(
                        context.actor,
                        selected.template_name,
                        assay_type,
                        _template_layout(assay_type, frame),
                        template_id=selected.template_id,
                        expected_updated_at=selected.updated_at,
                    )
                )
                st.session_state[f"{control_key}_flash"] = (
                    f"Updated template: {selected.template_name}."
                )
                st.rerun()
            except Exception as error:
                st.error(f"Could not overwrite plate template: {error}")

        confirmed = st.checkbox(
            "Confirm template deletion",
            key=f"{control_key}_confirm_delete",
            disabled=selected is None,
        )
        if delete.button(
            "Delete selected template",
            disabled=selected is None or not confirmed,
            key=f"{control_key}_delete",
        ):
            try:
                if selected is None:
                    raise ValueError("Choose a template to delete")
                DeletePlateTemplateService(context.repository).execute(
                    DeletePlateTemplate(
                        context.actor,
                        selected.template_id,
                        selected.updated_at,
                    )
                )
                st.session_state.pop(f"{control_key}_selected", None)
                st.session_state[f"{control_key}_flash"] = (
                    f"Deleted template: {selected.template_name}."
                )
                st.rerun()
            except Exception as error:
                st.error(f"Could not delete plate template: {error}")


def _template_layout(assay_type: AssayType, frame: pd.DataFrame) -> tuple[dict[str, object], ...]:
    if assay_type is AssayType.GROWTH:
        return growth_template_layout(frame)
    if assay_type is AssayType.MIC:
        return mic_template_layout(frame)
    raise ValueError(f"Plate templates are not supported for {assay_type.value} assays")
