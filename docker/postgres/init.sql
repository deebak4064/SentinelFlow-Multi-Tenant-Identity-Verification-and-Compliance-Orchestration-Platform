-- Runs once on first container start (docker-entrypoint-initdb.d). Schema itself comes from Alembic.
ALTER DATABASE sentinelflow SET timezone TO 'UTC';
ALTER DATABASE sentinelflow SET statement_timeout TO '30s';
-- Least privilege: the application role cannot create objects in other schemas.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CREATE ON SCHEMA public TO sentinel;
