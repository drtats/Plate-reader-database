# ADR-0020: Use Arrow's system memory pool for Streamlit editors

- Status: accepted
- Date: 2026-08-01
- Owners: integration owner

## Context

On macOS ARM, opening the shared plate editor after saving import metadata could
terminate the Python process with `Segmentation fault: 11`. The native stack
ended in `pyarrow.pandas_compat.convert_column` while Streamlit serialized the
8x12 `st.data_editor`. Because this is a native allocator crash, Python exception
handling and Streamlit error messages cannot intercept it.

The failure reproduced deterministically on the second in-process editor render
with Python 3.12, Streamlit 1.54, and PyArrow 25 using Arrow's default `mimalloc`
pool. The same workload completed 100 consecutive renders with Arrow's system
memory pool.

## Decision

Configure PyArrow to use `system_memory_pool()` at application startup and again
at the shared editor boundary. Export `ARROW_DEFAULT_MEMORY_POOL=system` from the
double-click launcher so the safe allocator is selected before Python imports.
Declare PyArrow as a direct dependency because application code now configures
its runtime API.

Keep the existing pandas/Arrow column normalization. This decision changes only
the native allocator; it does not change editor values, database writes, or the
shared 8x12 plus 96-row UI.

## Consequences

Standalone, local, test, and hosted execution use one stable allocator for Arrow
widget serialization. There may be a small allocation-performance difference,
but the editor workload is tiny relative to raw measurement storage and process
stability takes priority.

## Verification

An out-of-process regression test removes the environment override, renders the
real dual-view editor twenty times, and checks that the application boundary
selected the system pool. Running this in a subprocess means a future native
segmentation fault becomes an ordinary failing test instead of terminating the
main test suite.
