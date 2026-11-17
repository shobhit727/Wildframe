from datetime import UTC, datetime


def naive_utc_now() -> datetime:
    """Return UTC now as naive datetime for TIMESTAMP WITHOUT TIME ZONE columns."""
    return datetime.now(UTC).replace(tzinfo=None)
