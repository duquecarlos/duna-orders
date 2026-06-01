from pathlib import Path

from alembic.config import Config

from duna_orders.storage.postgres_base import Base


def test_alembic_config_loads_from_ini() -> None:
    config = Config("alembic.ini")

    assert config.get_main_option("script_location") == "alembic"


def test_alembic_file_template_is_deterministic() -> None:
    ini_text = Path("alembic.ini").read_text(encoding="utf-8")

    assert (
        "file_template = "
        "%%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d-%%(rev)s_%%(slug)s"
        in ini_text
    )


def test_alembic_env_uses_project_metadata() -> None:
    env_py = Path("alembic/env.py").read_text(encoding="utf-8")

    assert "from duna_orders.storage.postgres_base import Base" in env_py
    assert "target_metadata = Base.metadata" in env_py
    assert Base.metadata is not None


def test_alembic_env_reads_database_url_from_settings() -> None:
    env_py = Path("alembic/env.py").read_text(encoding="utf-8")

    assert "from duna_orders.config import settings" in env_py
    assert "settings.database_url" in env_py
    assert "driver://user:pass@localhost/dbname" not in env_py


def test_alembic_env_enables_autogenerate_comparisons() -> None:
    env_py = Path("alembic/env.py").read_text(encoding="utf-8")

    assert env_py.count("compare_type=True") == 2
    assert env_py.count("compare_server_default=True") == 2
    assert "include_schemas" not in env_py
    assert "version_table_schema" not in env_py