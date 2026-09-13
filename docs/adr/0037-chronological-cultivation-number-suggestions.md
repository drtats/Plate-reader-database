# ADR 0037: Suggest cultivation numbers in experiment-date order

Status: accepted, 2026-09-12. Updates the suggestion policy in ADR 0036.

The previous next-saved-number rule displayed 001 for every unnumbered Growth run.
Users need distinct chronological suggestions before saving any cultivation IDs.

`suggested_cultivation_experiment_code(repository, plate_id)` now plans numbers for
all unnumbered Growth plates in the database. It orders valid experiment dates
oldest first, then plate creation timestamp and plate ID to break ties. Missing or
invalid experiment dates sort last. The metadata-only `growth_cultivation_codes`
projection labels plate/well records and includes experiment date and plate creation
time on plate records; it does not read observations.

Already saved positive numeric shared/per-well codes remain reserved. Unnumbered
plates receive the smallest available positive numbers in chronological order,
padded to at least three digits. Thus opening the newest run first still shows its
position in the sequence, rather than 001. Saving a suggested number does not change
the planned numbers of the remaining runs. Soft-deleted Growth plates remain in the
projection, retaining the existing reservation policy. Each plate/run gets a number;
its wells share that number while retaining strain, position and replicate in IDs.

Suggestions are read-only. Date corrections or importing earlier experiments may
change unsaved suggestions; saved shared/per-well codes and exported IDs remain
fixed. The existing save transaction checks reservations again. No automatic writes
on reruns, schema migrations, or rewrites of saved cultivation IDs are introduced.
