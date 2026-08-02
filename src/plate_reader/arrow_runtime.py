"""Process-level Arrow configuration for stable Streamlit DataFrame widgets."""

from __future__ import annotations


def configure_arrow_memory_pool() -> str:
    """Use the system allocator to avoid native mimalloc crashes on macOS ARM."""

    import pyarrow as pa

    pool = pa.system_memory_pool()
    pa.set_memory_pool(pool)
    return str(pa.default_memory_pool().backend_name)
