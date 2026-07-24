"""Database URL compatibility for the installed PostgreSQL driver."""


def with_psycopg_driver(url: str) -> str:
    """Select psycopg 3 when SQLAlchemy receives its generic PostgreSQL URL."""
    return url.replace("postgresql://", "postgresql+psycopg://", 1)
