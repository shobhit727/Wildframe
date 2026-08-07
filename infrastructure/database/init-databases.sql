-- Database initialization script for Wildframe services
-- Run this against the main PostgreSQL instance to create service-specific databases

-- Create databases for each service (PostgreSQL doesn't support IF NOT EXISTS for CREATE DATABASE)
-- Use \l meta-command to list databases and conditionally create
\set ON_ERROR_STOP off
CREATE DATABASE auth_db;
CREATE DATABASE users_db;
CREATE DATABASE content_db;
CREATE DATABASE streaming_db;
CREATE DATABASE search_db;
CREATE DATABASE recommendation_db;
CREATE DATABASE billing_db;
CREATE DATABASE analytics_db;
CREATE DATABASE notification_db;
CREATE DATABASE media_db;
CREATE DATABASE admin_db;
CREATE DATABASE creators_db;
CREATE DATABASE moderation_db;
CREATE DATABASE uploads_db;
CREATE DATABASE wildframe;
CREATE DATABASE wildframe_db;
\set ON_ERROR_STOP on

-- Create service users with limited privileges
CREATE USER auth_user WITH PASSWORD 'auth_service_secure_password';
CREATE USER users_user WITH PASSWORD 'users_service_secure_password';
CREATE USER content_user WITH PASSWORD 'content_service_secure_password';
CREATE USER streaming_user WITH PASSWORD 'streaming_service_secure_password';
CREATE USER search_user WITH PASSWORD 'search_service_secure_password';
CREATE USER recommendation_user WITH PASSWORD 'recommendation_service_secure_password';
CREATE USER billing_user WITH PASSWORD 'billing_service_secure_password';
CREATE USER analytics_user WITH PASSWORD 'analytics_service_secure_password';
CREATE USER notification_user WITH PASSWORD 'notification_service_secure_password';
CREATE USER media_user WITH PASSWORD 'media_service_secure_password';
CREATE USER admin_user WITH PASSWORD 'admin_service_secure_password';
CREATE USER creators_user WITH PASSWORD 'creators_service_secure_password';
CREATE USER moderation_user WITH PASSWORD 'moderation_service_secure_password';
CREATE USER uploads_user WITH PASSWORD 'uploads_service_secure_password';

-- Grant privileges to service users on their respective databases
GRANT ALL PRIVILEGES ON DATABASE auth_db TO auth_user;
GRANT ALL PRIVILEGES ON DATABASE users_db TO users_user;
GRANT ALL PRIVILEGES ON DATABASE content_db TO content_user;
GRANT ALL PRIVILEGES ON DATABASE streaming_db TO streaming_user;
GRANT ALL PRIVILEGES ON DATABASE search_db TO search_user;
GRANT ALL PRIVILEGES ON DATABASE recommendation_db TO recommendation_user;
GRANT ALL PRIVILEGES ON DATABASE billing_db TO billing_user;
GRANT ALL PRIVILEGES ON DATABASE analytics_db TO analytics_user;
GRANT ALL PRIVILEGES ON DATABASE notification_db TO notification_user;
GRANT ALL PRIVILEGES ON DATABASE media_db TO media_user;
GRANT ALL PRIVILEGES ON DATABASE admin_db TO admin_user;
GRANT ALL PRIVILEGES ON DATABASE creators_db TO creators_user;
GRANT ALL PRIVILEGES ON DATABASE moderation_db TO moderation_user;
GRANT ALL PRIVILEGES ON DATABASE uploads_db TO uploads_user;
GRANT ALL PRIVILEGES ON DATABASE wildframe_db TO auth_user;

-- Connect to each database and set schema privileges
\c auth_db
ALTER SCHEMA public OWNER TO auth_user;
GRANT USAGE ON SCHEMA public TO auth_user;
GRANT CREATE ON SCHEMA public TO auth_user;
ALTER DEFAULT PRIVILEGES FOR USER auth_user IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO auth_user;
ALTER DEFAULT PRIVILEGES FOR USER auth_user IN SCHEMA public GRANT USAGE ON SEQUENCES TO auth_user;

\c users_db
ALTER SCHEMA public OWNER TO users_user;
GRANT USAGE ON SCHEMA public TO users_user;
GRANT CREATE ON SCHEMA public TO users_user;
ALTER DEFAULT PRIVILEGES FOR USER users_user IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO users_user;
ALTER DEFAULT PRIVILEGES FOR USER users_user IN SCHEMA public GRANT USAGE ON SEQUENCES TO users_user;

\c content_db
ALTER SCHEMA public OWNER TO content_user;
GRANT USAGE ON SCHEMA public TO content_user;
GRANT CREATE ON SCHEMA public TO content_user;
ALTER DEFAULT PRIVILEGES FOR USER content_user IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO content_user;
ALTER DEFAULT PRIVILEGES FOR USER content_user IN SCHEMA public GRANT USAGE ON SEQUENCES TO content_user;

\c streaming_db
ALTER SCHEMA public OWNER TO streaming_user;
GRANT USAGE ON SCHEMA public TO streaming_user;
GRANT CREATE ON SCHEMA public TO streaming_user;
ALTER DEFAULT PRIVILEGES FOR USER streaming_user IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO streaming_user;
ALTER DEFAULT PRIVILEGES FOR USER streaming_user IN SCHEMA public GRANT USAGE ON SEQUENCES TO streaming_user;

\c billing_db
ALTER SCHEMA public OWNER TO billing_user;
GRANT USAGE ON SCHEMA public TO billing_user;
GRANT CREATE ON SCHEMA public TO billing_user;
ALTER DEFAULT PRIVILEGES FOR USER billing_user IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO billing_user;
ALTER DEFAULT PRIVILEGES FOR USER billing_user IN SCHEMA public GRANT USAGE ON SEQUENCES TO billing_user;

\c analytics_db
ALTER SCHEMA public OWNER TO analytics_user;
GRANT USAGE ON SCHEMA public TO analytics_user;
GRANT CREATE ON SCHEMA public TO analytics_user;
ALTER DEFAULT PRIVILEGES FOR USER analytics_user IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO analytics_user;
ALTER DEFAULT PRIVILEGES FOR USER analytics_user IN SCHEMA public GRANT USAGE ON SEQUENCES TO analytics_user;

\c admin_db
ALTER SCHEMA public OWNER TO admin_user;
GRANT USAGE ON SCHEMA public TO admin_user;
GRANT CREATE ON SCHEMA public TO admin_user;
ALTER DEFAULT PRIVILEGES FOR USER admin_user IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO admin_user;
ALTER DEFAULT PRIVILEGES FOR USER admin_user IN SCHEMA public GRANT USAGE ON SEQUENCES TO admin_user;

\c creators_db
ALTER SCHEMA public OWNER TO creators_user;
GRANT USAGE ON SCHEMA public TO creators_user;
GRANT CREATE ON SCHEMA public TO creators_user;
ALTER DEFAULT PRIVILEGES FOR USER creators_user IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO creators_user;
ALTER DEFAULT PRIVILEGES FOR USER creators_user IN SCHEMA public GRANT USAGE ON SEQUENCES TO creators_user;

\c moderation_db
ALTER SCHEMA public OWNER TO moderation_user;
GRANT USAGE ON SCHEMA public TO moderation_user;
GRANT CREATE ON SCHEMA public TO moderation_user;
ALTER DEFAULT PRIVILEGES FOR USER moderation_user IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO moderation_user;
ALTER DEFAULT PRIVILEGES FOR USER moderation_user IN SCHEMA public GRANT USAGE ON SEQUENCES TO moderation_user;

\c uploads_db
ALTER SCHEMA public OWNER TO uploads_user;
GRANT USAGE ON SCHEMA public TO uploads_user;
GRANT CREATE ON SCHEMA public TO uploads_user;
ALTER DEFAULT PRIVILEGES FOR USER uploads_user IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO uploads_user;
ALTER DEFAULT PRIVILEGES FOR USER uploads_user IN SCHEMA public GRANT USAGE ON SEQUENCES TO uploads_user;

\c search_db
ALTER SCHEMA public OWNER TO search_user;
GRANT USAGE ON SCHEMA public TO search_user;
GRANT CREATE ON SCHEMA public TO search_user;
ALTER DEFAULT PRIVILEGES FOR USER search_user IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO search_user;
ALTER DEFAULT PRIVILEGES FOR USER search_user IN SCHEMA public GRANT USAGE ON SEQUENCES TO search_user;

\c recommendation_db
ALTER SCHEMA public OWNER TO recommendation_user;
GRANT USAGE ON SCHEMA public TO recommendation_user;
GRANT CREATE ON SCHEMA public TO recommendation_user;
ALTER DEFAULT PRIVILEGES FOR USER recommendation_user IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO recommendation_user;
ALTER DEFAULT PRIVILEGES FOR USER recommendation_user IN SCHEMA public GRANT USAGE ON SEQUENCES TO recommendation_user;

\c notification_db
ALTER SCHEMA public OWNER TO notification_user;
GRANT USAGE ON SCHEMA public TO notification_user;
GRANT CREATE ON SCHEMA public TO notification_user;
ALTER DEFAULT PRIVILEGES FOR USER notification_user IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO notification_user;
ALTER DEFAULT PRIVILEGES FOR USER notification_user IN SCHEMA public GRANT USAGE ON SEQUENCES TO notification_user;

\c media_db
ALTER SCHEMA public OWNER TO media_user;
GRANT USAGE ON SCHEMA public TO media_user;
GRANT CREATE ON SCHEMA public TO media_user;
ALTER DEFAULT PRIVILEGES FOR USER media_user IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO media_user;
ALTER DEFAULT PRIVILEGES FOR USER media_user IN SCHEMA public GRANT USAGE ON SEQUENCES TO media_user;

-- Create shared read-only user for analytics/reporting
CREATE USER analytics_reader WITH PASSWORD 'analytics_reader_password';
GRANT CONNECT ON DATABASE auth_db TO analytics_reader;
GRANT CONNECT ON DATABASE users_db TO analytics_reader;
GRANT CONNECT ON DATABASE content_db TO analytics_reader;
GRANT CONNECT ON DATABASE streaming_db TO analytics_reader;
GRANT CONNECT ON DATABASE billing_db TO analytics_reader;
GRANT CONNECT ON DATABASE creators_db TO analytics_reader;
GRANT CONNECT ON DATABASE moderation_db TO analytics_reader;
GRANT CONNECT ON DATABASE uploads_db TO analytics_reader;
GRANT CONNECT ON DATABASE search_db TO analytics_reader;
GRANT CONNECT ON DATABASE recommendation_db TO analytics_reader;
GRANT CONNECT ON DATABASE notification_db TO analytics_reader;
GRANT CONNECT ON DATABASE media_db TO analytics_reader;

-- Enable required extensions
\c auth_db
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "btree_gin";

\c users_db
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

\c content_db
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

\c streaming_db
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

\c billing_db
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

\c analytics_db
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

\c admin_db
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create monitoring user for metrics
CREATE USER metrics_user WITH PASSWORD 'metrics_password';
GRANT CONNECT ON DATABASE auth_db TO metrics_user;
GRANT CONNECT ON DATABASE users_db TO metrics_user;
GRANT CONNECT ON DATABASE content_db TO metrics_user;
GRANT CONNECT ON DATABASE streaming_db TO metrics_user;
GRANT CONNECT ON DATABASE billing_db TO metrics_user;
GRANT CONNECT ON DATABASE creators_db TO metrics_user;
GRANT CONNECT ON DATABASE moderation_db TO metrics_user;
GRANT CONNECT ON DATABASE uploads_db TO metrics_user;
GRANT CONNECT ON DATABASE search_db TO metrics_user;
GRANT CONNECT ON DATABASE recommendation_db TO metrics_user;
GRANT CONNECT ON DATABASE notification_db TO metrics_user;
GRANT CONNECT ON DATABASE media_db TO metrics_user;

-- Performance tuning
\c auth_db
ALTER SYSTEM SET max_connections = 200;
ALTER SYSTEM SET shared_buffers = '256MB';
ALTER SYSTEM SET effective_cache_size = '1GB';
ALTER SYSTEM SET maintenance_work_mem = '64MB';
ALTER SYSTEM SET checkpoint_completion_target = 0.9;
ALTER SYSTEM SET wal_buffers = '16MB';
ALTER SYSTEM SET default_statistics_target = 100;

-- Commit
\c postgres
SELECT pg_reload_conf();

-- Log initialization complete
SELECT NOW() as initialization_time, 'Database initialization complete' as status;
