PRAGMA foreign_keys = ON;

CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('viewer', 'editor', 'admin')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE experiments (
    experiment_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    project TEXT,
    experiment_date TEXT NOT NULL,
    operator_name TEXT,
    reader TEXT,
    incubation_time_hours REAL,
    inoculum_od REAL,
    growth_phase TEXT,
    harvest_od REAL,
    doubling_time_minutes REAL,
    notes TEXT,
    custom_json TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL REFERENCES users(user_id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE experiment_tags (
    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    tag TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (experiment_id, tag)
);

CREATE TABLE plates (
    plate_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    assay_type TEXT NOT NULL CHECK (assay_type IN ('growth', 'mic')),
    plate_name TEXT NOT NULL,
    plate_format INTEGER NOT NULL DEFAULT 96 CHECK (plate_format > 0),
    lifecycle_status TEXT NOT NULL DEFAULT 'draft'
        CHECK (lifecycle_status IN ('draft', 'final', 'archived')),
    instrument TEXT,
    channel TEXT,
    temperature REAL,
    temperature_unit TEXT,
    manual_subtraction REAL NOT NULL DEFAULT 0,
    threshold REAL,
    threshold_method TEXT,
    background_method TEXT,
    is_locked INTEGER NOT NULL DEFAULT 0 CHECK (is_locked IN (0, 1)),
    is_checked INTEGER NOT NULL DEFAULT 0 CHECK (is_checked IN (0, 1)),
    legacy_run_id TEXT,
    custom_json TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL REFERENCES users(user_id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    deleted_by TEXT REFERENCES users(user_id),
    CHECK ((deleted_at IS NULL) = (deleted_by IS NULL))
);

CREATE TABLE wells (
    well_id TEXT PRIMARY KEY,
    plate_id TEXT NOT NULL REFERENCES plates(plate_id),
    position TEXT NOT NULL COLLATE NOCASE,
    row_index INTEGER NOT NULL CHECK (row_index >= 0),
    column_index INTEGER NOT NULL CHECK (column_index >= 0),
    raw_label TEXT,
    display_name TEXT,
    is_blank INTEGER NOT NULL DEFAULT 0 CHECK (is_blank IN (0, 1)),
    background_group TEXT,
    plot_selected INTEGER NOT NULL DEFAULT 0 CHECK (plot_selected IN (0, 1)),
    notes TEXT,
    custom_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (plate_id, position),
    UNIQUE (plate_id, row_index, column_index)
);

CREATE TABLE well_conditions (
    well_id TEXT PRIMARY KEY REFERENCES wells(well_id),
    strain TEXT,
    medium TEXT,
    replicate INTEGER CHECK (replicate IS NULL OR replicate > 0),
    inoculum_size REAL,
    inoculum_unit TEXT,
    grouping_label TEXT,
    treatment TEXT,
    concentration REAL,
    concentration_unit TEXT,
    custom_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE import_sources (
    source_id TEXT PRIMARY KEY,
    plate_id TEXT REFERENCES plates(plate_id),
    source_kind TEXT NOT NULL CHECK (
        source_kind IN ('growth_csv', 'growth_labels', 'mic_plate', 'legacy_growth',
                        'legacy_mic', 'portable')
    ),
    original_filename TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    parser_version TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('pending', 'imported', 'failed')),
    imported_by TEXT NOT NULL REFERENCES users(user_id),
    imported_at TEXT NOT NULL,
    custom_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE growth_measurements (
    plate_id TEXT NOT NULL REFERENCES plates(plate_id),
    well_id TEXT NOT NULL REFERENCES wells(well_id),
    channel TEXT NOT NULL,
    time_index INTEGER NOT NULL CHECK (time_index >= 0),
    elapsed_microseconds INTEGER NOT NULL CHECK (elapsed_microseconds >= 0),
    value_raw REAL,
    PRIMARY KEY (plate_id, well_id, channel, time_index),
    UNIQUE (plate_id, well_id, channel, elapsed_microseconds)
);

CREATE TABLE analysis_revisions (
    revision_id TEXT PRIMARY KEY,
    plate_id TEXT NOT NULL REFERENCES plates(plate_id),
    assay_type TEXT NOT NULL CHECK (assay_type IN ('growth', 'mic')),
    algorithm_name TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    parameters_json TEXT NOT NULL DEFAULT '{}',
    input_sha256 TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    created_by TEXT NOT NULL REFERENCES users(user_id),
    created_at TEXT NOT NULL
);

CREATE TABLE growth_backgrounds (
    revision_id TEXT NOT NULL REFERENCES analysis_revisions(revision_id),
    background_group TEXT NOT NULL,
    channel TEXT NOT NULL,
    time_index INTEGER NOT NULL CHECK (time_index >= 0),
    elapsed_microseconds INTEGER NOT NULL CHECK (elapsed_microseconds >= 0),
    mean_value REAL NOT NULL,
    std_value REAL,
    coefficient_of_variation REAL,
    blank_count INTEGER NOT NULL CHECK (blank_count > 0),
    qc_status TEXT NOT NULL CHECK (qc_status IN ('good', 'caution', 'high_cv', 'missing')),
    PRIMARY KEY (revision_id, background_group, channel, time_index)
);

CREATE TABLE growth_metrics (
    revision_id TEXT NOT NULL REFERENCES analysis_revisions(revision_id),
    well_id TEXT NOT NULL REFERENCES wells(well_id),
    channel TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL,
    metric_unit TEXT,
    quality_flag TEXT,
    PRIMARY KEY (revision_id, well_id, channel, metric_name)
);

CREATE TABLE mic_readings (
    plate_id TEXT NOT NULL REFERENCES plates(plate_id),
    well_id TEXT NOT NULL REFERENCES wells(well_id),
    channel TEXT NOT NULL DEFAULT 'od',
    value_raw REAL,
    PRIMARY KEY (plate_id, well_id, channel)
);

CREATE TABLE mic_well_calls (
    revision_id TEXT NOT NULL REFERENCES analysis_revisions(revision_id),
    well_id TEXT NOT NULL REFERENCES wells(well_id),
    background_value REAL NOT NULL,
    value_background_subtracted REAL NOT NULL,
    growth_call INTEGER CHECK (growth_call IN (0, 1)),
    PRIMARY KEY (revision_id, well_id)
);

CREATE TABLE mic_results (
    result_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES analysis_revisions(revision_id),
    group_key TEXT NOT NULL,
    strain TEXT NOT NULL,
    treatment TEXT NOT NULL,
    medium TEXT NOT NULL,
    replicate INTEGER NOT NULL CHECK (replicate > 0),
    mic_value REAL NOT NULL,
    mic_operator TEXT NOT NULL CHECK (mic_operator IN ('=', '>', '<', '<=')),
    mic_unit TEXT NOT NULL,
    threshold_used REAL NOT NULL,
    lowest_tested_concentration REAL NOT NULL,
    highest_tested_concentration REAL NOT NULL,
    concentrations_json TEXT NOT NULL,
    point_count INTEGER NOT NULL CHECK (point_count > 0),
    calculation_status TEXT NOT NULL,
    warning TEXT,
    UNIQUE (revision_id, group_key)
);

CREATE TABLE plate_templates (
    template_id TEXT PRIMARY KEY,
    template_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    assay_type TEXT NOT NULL CHECK (assay_type IN ('growth', 'mic')),
    layout_json TEXT NOT NULL,
    created_by TEXT NOT NULL REFERENCES users(user_id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE saved_options (
    option_type TEXT NOT NULL,
    value TEXT NOT NULL COLLATE NOCASE,
    created_by TEXT NOT NULL REFERENCES users(user_id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (option_type, value)
);

CREATE TABLE provenance_events (
    event_id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL REFERENCES users(user_id),
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_experiments_date ON experiments(experiment_date DESC);
CREATE INDEX idx_experiments_project ON experiments(project, experiment_date DESC);
CREATE INDEX idx_plates_list ON plates(deleted_at, updated_at DESC);
CREATE INDEX idx_plates_experiment ON plates(experiment_id, assay_type);
CREATE INDEX idx_wells_plate ON wells(plate_id, row_index, column_index);
CREATE INDEX idx_conditions_search
    ON well_conditions(strain, medium, treatment, concentration);
CREATE INDEX idx_import_sources_hash ON import_sources(content_sha256, source_kind);
CREATE INDEX idx_growth_measurements_load
    ON growth_measurements(plate_id, channel, time_index, well_id);
CREATE INDEX idx_growth_backgrounds_load
    ON growth_backgrounds(revision_id, channel, time_index);
CREATE UNIQUE INDEX idx_current_analysis_revision
    ON analysis_revisions(plate_id, algorithm_name) WHERE is_current = 1;
CREATE INDEX idx_mic_results_search
    ON mic_results(strain, treatment, medium, mic_value);
CREATE INDEX idx_provenance_entity
    ON provenance_events(entity_type, entity_id, occurred_at DESC);

CREATE TRIGGER prevent_growth_measurement_update
BEFORE UPDATE ON growth_measurements
BEGIN
    SELECT RAISE(ABORT, 'raw growth measurements are immutable');
END;

CREATE TRIGGER prevent_growth_measurement_delete
BEFORE DELETE ON growth_measurements
BEGIN
    SELECT RAISE(ABORT, 'raw growth measurements are immutable');
END;

CREATE TRIGGER prevent_mic_reading_update
BEFORE UPDATE ON mic_readings
BEGIN
    SELECT RAISE(ABORT, 'raw MIC readings are immutable');
END;

CREATE TRIGGER prevent_mic_reading_delete
BEFORE DELETE ON mic_readings
BEGIN
    SELECT RAISE(ABORT, 'raw MIC readings are immutable');
END;

CREATE TRIGGER prevent_provenance_update
BEFORE UPDATE ON provenance_events
BEGIN
    SELECT RAISE(ABORT, 'provenance events are append-only');
END;

CREATE TRIGGER prevent_provenance_delete
BEFORE DELETE ON provenance_events
BEGIN
    SELECT RAISE(ABORT, 'provenance events are append-only');
END;
