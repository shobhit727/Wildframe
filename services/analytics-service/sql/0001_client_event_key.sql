-- Artifact only: NOT applied by any tooling in this repo. Run it by hand, once per
-- existing analytics_db, BEFORE the analytics-service image that carries #1143
-- takes traffic:
--
--   psql -v ON_ERROR_STOP=1 -d analytics_db -f services/analytics-service/sql/0001_client_event_key.sql
--
-- Why: events.client_event_key and content_view_events.client_event_key are the
-- unique idempotency keys that replaced the Redis SET NX dedup (#1143); inserts
-- use ON CONFLICT (client_event_key) DO NOTHING. scripts/init_schemas.py creates
-- them on a fresh database, but refuses to add a unique column to a table that
-- already exists ("cannot safely add ...: it carries unique"), so an existing
-- database needs these statements. Until they run, every insert into and every
-- ORM read of these two tables fails with UndefinedColumnError.
--
-- Order matters: the old image never writes the column, so applying this first is
-- zero-downtime. CREATE INDEX CONCURRENTLY cannot run inside a transaction, so use
-- psql -f as above (autocommit), not BEGIN/COMMIT. The new columns are all NULL,
-- and NULLs never collide, so the unique index build cannot fail on existing rows.
--
-- Safe to run twice, and a no-op on a database init_schemas.py created: the names
-- match the ones create_all gives the column-level unique constraint. If a
-- CONCURRENTLY build is interrupted it leaves an INVALID index that IF NOT EXISTS
-- will skip and ADD CONSTRAINT will reject; DROP INDEX it and run the file again.

ALTER TABLE events ADD COLUMN IF NOT EXISTS client_event_key VARCHAR(64);
ALTER TABLE content_view_events ADD COLUMN IF NOT EXISTS client_event_key VARCHAR(64);

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS events_client_event_key_key
    ON events (client_event_key);
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS content_view_events_client_event_key_key
    ON content_view_events (client_event_key);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'events_client_event_key_key') THEN
        ALTER TABLE events
            ADD CONSTRAINT events_client_event_key_key
            UNIQUE USING INDEX events_client_event_key_key;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'content_view_events_client_event_key_key'
    ) THEN
        ALTER TABLE content_view_events
            ADD CONSTRAINT content_view_events_client_event_key_key
            UNIQUE USING INDEX content_view_events_client_event_key_key;
    END IF;
END
$$;
