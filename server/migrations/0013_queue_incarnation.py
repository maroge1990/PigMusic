"""Each queue entry gets its own random incarnation when it is created, so
Cancel all can name exactly the entries it was pressed on: an entry deleted
and submitted again (same deterministic id, same second), or a cancelled or
failed one asked for again (the row is reused), is a new one (D-79, #183)."""


def up(conn):
    conn.execute("ALTER TABLE queue ADD COLUMN incarnation TEXT")
    conn.execute("UPDATE queue SET incarnation=lower(hex(randomblob(16)))")
    # Every insert, wherever it happens, gets one; payload updates keep it.
    conn.execute(
        """CREATE TRIGGER queue_incarnation AFTER INSERT ON queue
        WHEN NEW.incarnation IS NULL
        BEGIN
            UPDATE queue SET incarnation=lower(hex(randomblob(16)))
            WHERE rowid=NEW.rowid;
        END"""
    )
    # A cancelled or failed entry asked for again reuses its row: it is a new
    # request, whichever path revives it (submission, retry, policy).
    conn.execute(
        """CREATE TRIGGER queue_revived AFTER UPDATE OF status ON queue
        WHEN OLD.status IN ('cancelled','failed')
            AND NEW.status NOT IN ('cancelled','failed')
        BEGIN
            UPDATE queue SET incarnation=lower(hex(randomblob(16)))
            WHERE rowid=NEW.rowid;
        END"""
    )
