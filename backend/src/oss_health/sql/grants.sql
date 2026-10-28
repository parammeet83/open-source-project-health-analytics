-- Least-privilege grants. Idempotent; executed by the schema owner (migration credential) after
-- every migration and by `oss-health db bootstrap-roles`. Roles that do not exist are skipped.
--
--   oss_health_app    : pipeline writer. SELECT/INSERT/UPDATE on oss_health tables, DELETE only
--                       on rejected_records (retention). No TRUNCATE, no DDL, no analytics access.
--   oss_health_reader : public API / Power BI. SELECT on analytics views only.
DO $grants$
BEGIN
    EXECUTE 'REVOKE ALL ON SCHEMA oss_health FROM PUBLIC';
    EXECUTE 'REVOKE ALL ON SCHEMA analytics FROM PUBLIC';

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'oss_health_app') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA oss_health TO oss_health_app';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA oss_health TO oss_health_app';
        EXECUTE 'GRANT DELETE ON oss_health.rejected_records TO oss_health_app';
        EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA oss_health TO oss_health_app';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA oss_health GRANT SELECT, INSERT, UPDATE ON TABLES TO oss_health_app';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA oss_health GRANT USAGE, SELECT ON SEQUENCES TO oss_health_app';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'oss_health_reader') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA analytics TO oss_health_reader';
        EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO oss_health_reader';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO oss_health_reader';
    END IF;
END
$grants$;
