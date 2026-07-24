from app.infra.db.migrations.database_url import with_psycopg_driver


def test_common_postgresql_url_uses_installed_psycopg_driver() -> None:
    assert (
        with_psycopg_driver("postgresql://user:password@localhost/neuro")
        == "postgresql+psycopg://user:password@localhost/neuro"
    )


def test_explicit_driver_url_is_unchanged() -> None:
    url = "postgresql+psycopg://user:password@localhost/neuro"

    assert with_psycopg_driver(url) == url
