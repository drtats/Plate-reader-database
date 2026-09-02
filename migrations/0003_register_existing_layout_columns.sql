INSERT OR IGNORE INTO saved_options(option_type, value, created_by, created_at)
SELECT
    'layout_column:' || p.assay_type,
    MIN(TRIM(CAST(j.key AS TEXT))),
    MIN(p.created_by),
    MIN(w.created_at)
FROM wells AS w
JOIN plates AS p ON p.plate_id = w.plate_id
JOIN json_each(
    CASE WHEN json_valid(w.custom_json) THEN w.custom_json ELSE '{}' END
) AS j
WHERE p.deleted_at IS NULL
  AND TRIM(CAST(j.key AS TEXT)) <> ''
  AND NOT (
      p.assay_type = 'growth'
      AND LOWER(TRIM(CAST(j.key AS TEXT))) IN (
          'well', 'raw label', 'display name', 'blank', 'background group', 'plot',
          'group', 'media', 'strain', 'inoculum size', 'inoculum unit', 'replicate',
          'notes', 'treatment', 'concentration', 'concentration unit', 't0 added (min)',
          'cultivation short id', 'date time', 'culture age h', 'well row',
          'well column', 'culture volume ul', 'condition 1 state', 'condition 2 state',
          'condition 3 state', 'background subtracted od', 'microplate id',
          'background mean od', 'background sd od', 'background blank n',
          'background qc flag', 'background qc reason', 'run id', 'project',
          'experiment name', 'time min', 'signal type', 'raw od', 'bg group',
          'metadata level', 'experiment date', 'user', 'instrument', 'temperature',
          'source folder', 'editable metadata json', 'source metadata json', 'run_id',
          'display_name', 'inoculum_size', 'treatments', 'is_blank', 'bg_group',
          'row', 'col', 'raw_label', 'treatment_1', 'conc_1', 'unit_1',
          'treatment_2', 'conc_2', 'unit_2', 'treatment_3', 'conc_3', 'unit_3',
          't0_added_min'
      )
  )
  AND NOT (
      p.assay_type = 'mic'
      AND LOWER(TRIM(CAST(j.key AS TEXT))) IN (
          'well', 'raw od', 'display name', 'blank', 'strain',
          'antibiotic / treatment', 'concentration', 'concentration unit', 'media',
          'replicate', 'notes'
      )
  )
GROUP BY p.assay_type, LOWER(TRIM(CAST(j.key AS TEXT)));
