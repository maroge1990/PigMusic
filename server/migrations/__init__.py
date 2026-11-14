"""Explicit, atomic SQLite migration ledger; never silently downgrade state."""

from importlib import import_module

MIGRATIONS = (
    (1, "0001_baseline"),
    (2, "0002_accounts"),
    (3, "0003_personal_libraries"),
    (4, "0004_shared_policy"),
    (5, "0005_plex_accounts"),
    (6, "0006_native_sync"),
    (7, "0007_native_audio"),
    (8, "0008_device_copies"),
    (9, "0009_native_mutations"),
    (10, "0010_mixes"),
    (11, "0011_remove_plex"),
    (12, "0012_radio_feedback"),
    (13, "0013_queue_incarnation"),
)


def migrate(conn):
    if conn.in_transaction:
        raise RuntimeError("Migrations require an idle connection")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        known = {version for version, _ in MIGRATIONS}
        if applied - known:
            raise RuntimeError(
                "Database uses a newer schema; restore its compatible release"
            )
        if applied and applied != {v for v in known if v <= max(applied)}:
            raise RuntimeError("Incomplete migration ledger")
        for version, name in MIGRATIONS:
            if version not in applied:
                import_module(f"{__name__}.{name}").up(conn)
                conn.execute(
                    "INSERT INTO schema_migrations(version,applied_at) VALUES (?,CURRENT_TIMESTAMP)",
                    (version,),
                )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
