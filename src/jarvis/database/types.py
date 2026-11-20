"""Custom SQLAlchemy types for PostgreSQL-specific columns."""

from __future__ import annotations

from sqlalchemy.types import TypeEngine, UserDefinedType


class VECTOR(UserDefinedType):
    """pgvector ``VECTOR(n)`` column type.

    The dimension ``n`` is fixed per database at migration time and changed
    only via explicit migrations (see docs/ARCHITECTURE.md §7). ORM models
    read the dimension from the migration-managed schema via
    ``with_dimension`` at bind time.
    """

    cache_ok = True

    def __init__(self, dimension: int | None = None) -> None:
        self.dimension = dimension

    def get_col_spec(self, **kw: object) -> str:  # type: ignore[override]
        if self.dimension is not None:
            return f"VECTOR({self.dimension})"
        return "VECTOR"

    def bind_processor(self, dialect: TypeEngine):  # type: ignore[type-arg]
        def process(value: object):
            if value is None:
                return None
            if isinstance(value, (list, tuple)):
                return "[" + ",".join(str(v) for v in value) + "]"
            return str(value)

        return process

    def result_processor(self, dialect: TypeEngine):  # type: ignore[type-arg]
        def process(value: object):
            if value is None or not isinstance(value, str):
                return value
            inner = value.strip()[1:-1]
            return [float(x) for x in inner.split(",") if x.strip()]

        return process
