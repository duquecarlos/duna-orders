from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from duna_orders.config import Settings  # noqa: E402


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class MigrationState:
    current_revision: str | None
    head_revision: str


def _is_non_empty(value: str | None) -> bool:
    return value is not None and value.strip() != ""


def _is_https_url(value: str | None) -> bool:
    if not _is_non_empty(value):
        return False

    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _has_twilio_webhook_path(value: str | None) -> bool:
    if not _is_non_empty(value):
        return False

    parsed = urlparse(value)
    return parsed.path == "/webhooks/twilio/whatsapp"


def _mask_url_password(text: str, database_url: str | None) -> str:
    if not database_url:
        return text

    parsed = urlparse(database_url)
    password = parsed.password

    if not password:
        return text

    masked = text.replace(password, "****")
    encoded_password = quote(password, safe="")

    if encoded_password != password:
        masked = masked.replace(encoded_password, "****")

    return masked


def validate_settings(settings: Settings) -> list[CheckResult]:
    return [
        CheckResult(
            name="DUNA_STORAGE_BACKEND=postgres",
            passed=settings.duna_storage_backend == "postgres",
            detail="configured" if settings.duna_storage_backend == "postgres" else "expected postgres",
        ),
        CheckResult(
            name="DATABASE_URL present",
            passed=_is_non_empty(settings.database_url),
            detail="configured" if _is_non_empty(settings.database_url) else "missing or empty",
        ),
        CheckResult(
            name="twilio_auth_token present",
            passed=_is_non_empty(settings.twilio_auth_token),
            detail="configured" if _is_non_empty(settings.twilio_auth_token) else "missing or empty",
        ),
        CheckResult(
            name="twilio_webhook_public_url present",
            passed=_is_non_empty(settings.twilio_webhook_public_url),
            detail="configured"
            if _is_non_empty(settings.twilio_webhook_public_url)
            else "missing or empty",
        ),
        CheckResult(
            name="twilio_webhook_public_url is https",
            passed=_is_https_url(settings.twilio_webhook_public_url),
            detail="valid https URL" if _is_https_url(settings.twilio_webhook_public_url) else "invalid",
        ),
        CheckResult(
            name="twilio_webhook_public_url path",
            passed=_has_twilio_webhook_path(settings.twilio_webhook_public_url),
            detail=(
                "valid path"
                if _has_twilio_webhook_path(settings.twilio_webhook_public_url)
                else "expected /webhooks/twilio/whatsapp"
            ),
        ),
        CheckResult(
            name="webhook_tenant_id present",
            passed=_is_non_empty(settings.webhook_tenant_id),
            detail="configured" if _is_non_empty(settings.webhook_tenant_id) else "missing or empty",
        ),
    ]


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def check_database_url_engine(database_url: str) -> tuple[Engine | None, CheckResult]:
    try:
        engine = make_engine(database_url)
    except Exception as error:  # noqa: BLE001
        return None, CheckResult(
            name="database URL",
            passed=False,
            detail=f"malformed or unsupported ({type(error).__name__})",
        )

    return engine, CheckResult(name="database URL", passed=True, detail="accepted")


def check_database_connectivity(engine: Engine, database_url: str | None = None) -> CheckResult:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as error:  # noqa: BLE001
        detail = _mask_url_password(f"{type(error).__name__}: {error}", database_url)
        return CheckResult(
            name="database connectivity",
            passed=False,
            detail=detail,
        )

    return CheckResult(name="database connectivity", passed=True, detail="connected")


def get_alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def get_migration_state(engine: Engine, alembic_config: Config | None = None) -> MigrationState:
    config = alembic_config or get_alembic_config()
    script = ScriptDirectory.from_config(config)
    head_revisions = script.get_heads()

    if len(head_revisions) == 0:
        raise RuntimeError("Alembic has no head revision")

    if len(head_revisions) > 1:
        raise RuntimeError("multiple alembic heads detected; resolve before smoke")

    head_revision = head_revisions[0]

    with engine.connect() as connection:
        current_revision = MigrationContext.configure(connection).get_current_revision()

    return MigrationState(current_revision=current_revision, head_revision=head_revision)


def check_migration_state(state: MigrationState) -> CheckResult:
    current = state.current_revision or "base"
    detail = f"current={current}; head={state.head_revision}"
    return CheckResult(
        name="alembic revision state",
        passed=state.current_revision == state.head_revision,
        detail=detail,
    )


def print_check(result: CheckResult) -> None:
    status = "PASS" if result.passed else "FAIL"
    print(f"{status}: {result.name} - {result.detail}")


def run_preflight(settings: Settings | None = None) -> int:
    settings = settings or Settings()
    checks = validate_settings(settings)

    engine: Engine | None = None

    try:
        for check in checks:
            print_check(check)

        database_url = settings.database_url

        if _is_non_empty(database_url):
            cleaned_database_url = database_url.strip()
            engine, database_url_check = check_database_url_engine(cleaned_database_url)
            checks.append(database_url_check)
            print_check(database_url_check)

            if engine is not None:
                connectivity = check_database_connectivity(engine, cleaned_database_url)
                checks.append(connectivity)
                print_check(connectivity)
            else:
                connectivity = CheckResult(
                    name="database connectivity",
                    passed=False,
                    detail="skipped because database engine was not created",
                )
                checks.append(connectivity)
                print_check(connectivity)

            if connectivity.passed:
                try:
                    migration_state = get_migration_state(engine)
                    migration_check = check_migration_state(migration_state)
                except Exception as error:  # noqa: BLE001
                    migration_check = CheckResult(
                        name="alembic revision state",
                        passed=False,
                        detail=f"{type(error).__name__}: {error}",
                    )

                checks.append(migration_check)
                print_check(migration_check)

                if not migration_check.passed:
                    print("Required migration command:")
                    print("alembic upgrade head")
            else:
                migration_skip_detail = (
                    "skipped because database connectivity failed"
                    if engine is not None
                    else "skipped because database engine was not created"
                )
                checks.append(
                    CheckResult(
                        name="alembic revision state",
                        passed=False,
                        detail=migration_skip_detail,
                    )
                )
                print_check(checks[-1])
        else:
            checks.append(
                CheckResult(
                    name="database connectivity",
                    passed=False,
                    detail="skipped because DATABASE_URL is missing",
                )
            )
            print_check(checks[-1])
            checks.append(
                CheckResult(
                    name="alembic revision state",
                    passed=False,
                    detail="skipped because DATABASE_URL is missing",
                )
            )
            print_check(checks[-1])

        passed = sum(1 for check in checks if check.passed)
        total = len(checks)
        success = passed == total
        summary = "PASS" if success else "FAIL"
        print(f"SUMMARY: {summary} ({passed}/{total} checks passed)")
        return 0 if success else 1
    finally:
        if engine is not None:
            engine.dispose()


def main() -> int:
    return run_preflight()


if __name__ == "__main__":
    raise SystemExit(main())
