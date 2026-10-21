-- Artifact only: NOT applied by any tooling in this repo.
--
-- Why: app/models.py maps PipelineStageLog.status, and app/services.py writes it
-- on every stage attempt (see _record_stage), but any media_db that already has
-- a pipeline_stage_logs table created before that column existed will fail the
-- INSERT with "column status of relation pipeline_stage_logs does not exist".
-- There is no migration runner in this repository (infrastructure/database/
-- init-databases.sql only issues CREATE DATABASE; tests build the schema with
-- Base.metadata.create_all), so this statement has to be run by hand once per
-- existing environment.
--
-- Safe to run twice: it does nothing when the column already exists.
-- The column holds the enum *names* (SUCCESS/FAILED/SKIPPED) because that is
-- what sqlalchemy.Enum(PipelineStageStatus) persists; the API layer renders the
-- lowercase *.value* form. Rows written before the column existed get FAILED:
-- their outcome was never recorded.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'pipeline_stage_logs'
          AND column_name = 'status'
    ) THEN
        ALTER TABLE pipeline_stage_logs
            ADD COLUMN status VARCHAR(7) NOT NULL DEFAULT 'FAILED';
        COMMENT ON COLUMN pipeline_stage_logs.status IS
            'outcome of this attempt: SUCCESS, FAILED or SKIPPED';
    END IF;
END
$$;
