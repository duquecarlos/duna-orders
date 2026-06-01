import pytest
from sqlalchemy import text

from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_session import (
    make_engine,
    make_session_factory,
    session_scope,
)


def test_postgres_base_uses_stable_constraint_naming():
    assert Base.metadata.naming_convention["pk"] == "pk_%(table_name)s"
    assert Base.metadata.naming_convention["fk"] == (
        "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
    )


def test_make_engine_requires_database_url():
    with pytest.raises(ValueError, match="database_url is required"):
        make_engine("")


def test_session_scope_commits_successful_work():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    session_factory = make_session_factory(engine)

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE demo_rows (name TEXT NOT NULL)"))

    with session_scope(session_factory) as session:
        session.execute(text("INSERT INTO demo_rows (name) VALUES ('ok')"))

    with session_factory() as session:
        count = session.execute(text("SELECT COUNT(*) FROM demo_rows")).scalar_one()

    assert count == 1


def test_session_scope_rolls_back_failed_work():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    session_factory = make_session_factory(engine)

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE demo_rows (name TEXT NOT NULL)"))

    with pytest.raises(RuntimeError, match="boom"):
        with session_scope(session_factory) as session:
            session.execute(text("INSERT INTO demo_rows (name) VALUES ('rollback')"))
            raise RuntimeError("boom")

    with session_factory() as session:
        count = session.execute(text("SELECT COUNT(*) FROM demo_rows")).scalar_one()

    assert count == 0