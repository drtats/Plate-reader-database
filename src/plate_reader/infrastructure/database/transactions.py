"""Explicit transaction boundary shared by repository adapters."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from plate_reader.infrastructure.database.dbapi import Connection


class NestedTransactionError(RuntimeError):
    pass


@contextmanager
def transaction(connection: Connection) -> Iterator[None]:
    if connection.in_transaction:
        raise NestedTransactionError("Nested repository transactions are not supported")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()
