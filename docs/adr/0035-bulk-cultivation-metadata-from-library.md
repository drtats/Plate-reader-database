# ADR 0035: Edit shared cultivation metadata from the Growth Run Library

Status: accepted.

The Library selection form gains an explicit Edit cultivation metadata action.
It opens a metadata-only view of the selected Growth plates and their current
registry descriptions. The editor patches only explicitly selected shared fields,
with fill-empty-only and replace-selected-values modes. Empty strings can clear
selected fields in replace mode. The existing per-plate registry remains the source
for workspace editing and exports; no new global inheritance or schema is needed.

The new repository projection `growth_cultivation_metadata(plate_ids)` returns
`plate_id`, `experiment_name`, `plate_name`, `updated_at`, and `plate_custom_json`
for active Growth plates, without loading wells, measurements or revisions. Missing,
deleted and non-Growth IDs are omitted; the application rejects incomplete selections.
The bulk service merges each patch into each run's current registry and preserves
unrelated custom JSON. It validates selected fields, role and each preview's optimistic
version. All selected runs and before/after provenance events commit in one transaction;
a stale version or any write failure rolls back the entire batch. Even a no-op target
is checked for concurrent edits. Cultivation IDs and per-well overrides remain intact.
Changing shared Team/System values changes defaults for future ID generation, not
saved per-well identity components.

UI state retains the selected batch and submitted versions while editing; selecting a
new batch or searching resets the editor. Saving invalidates affected workspace and
Library/export caches. Merely selecting or sorting Library rows triggers no database
reads or writes. Only editor opening and saving read the metadata projection.

Verification covers both database backends, metadata-only access, field preservation,
fill/replace/clear behavior, authorization, missing/non-Growth selection, optimistic
conflicts, atomic rollback, audit history, exported metadata, and the Library UI flow.
