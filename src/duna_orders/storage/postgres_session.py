from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


_engine_cache_lock = Lock()
_engines_by_database_url: dict[str, Engine] = {}
_session_factories_by_database_url: dict[str, sessionmaker[Session]] = {}


def make_engine(database_url: str, *, echo: bool = False) -> Engine:
    if not database_url:
        raise ValueError("database_url is required")

    return create_engine(
        database_url,
        echo=echo,
        future=True,
        pool_pre_ping=True,
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )


def get_or_create_engine(database_url: str, *, echo: bool = False) -> Engine:
    if not database_url:
        raise ValueError("database_url is required")

    with _engine_cache_lock:
        engine = _engines_by_database_url.get(database_url)

        if engine is None:
            engine = make_engine(database_url, echo=echo)
            _engines_by_database_url[database_url] = engine

        return engine


def get_or_create_session_factory(
    database_url: str,
    *,
    echo: bool = False,
) -> sessionmaker[Session]:
    if not database_url:
        raise ValueError("database_url is required")

    with _engine_cache_lock:
        session_factory = _session_factories_by_database_url.get(database_url)

        if session_factory is None:
            engine = _engines_by_database_url.get(database_url)

            if engine is None:
                engine = make_engine(database_url, echo=echo)
                _engines_by_database_url[database_url] = engine

            session_factory = make_session_factory(engine)
            _session_factories_by_database_url[database_url] = session_factory

        return session_factory


def dispose_all_engines() -> None:
    with _engine_cache_lock:
        engines = list(_engines_by_database_url.values())
        _engines_by_database_url.clear()
        _session_factories_by_database_url.clear()

    for engine in engines:
        engine.dispose()


def reset_engine_cache() -> None:
    dispose_all_engines()


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()

    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()