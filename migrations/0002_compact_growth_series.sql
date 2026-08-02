CREATE TABLE growth_series_chunks (
    plate_id TEXT NOT NULL REFERENCES plates(plate_id),
    channel TEXT NOT NULL,
    positions_json TEXT NOT NULL,
    timepoints_blob BLOB NOT NULL,
    values_blob BLOB NOT NULL,
    timepoint_count INTEGER NOT NULL CHECK (timepoint_count > 0),
    position_count INTEGER NOT NULL CHECK (position_count > 0),
    encoding TEXT NOT NULL CHECK (encoding = 'zlib-f64-matrix-v1'),
    content_sha256 TEXT NOT NULL,
    PRIMARY KEY (plate_id, channel)
);

CREATE TRIGGER prevent_growth_series_chunk_update
BEFORE UPDATE ON growth_series_chunks
BEGIN
    SELECT RAISE(ABORT, 'raw growth series are immutable');
END;

CREATE TRIGGER prevent_growth_series_chunk_delete
BEFORE DELETE ON growth_series_chunks
BEGIN
    SELECT RAISE(ABORT, 'raw growth series are immutable');
END;
