# Database Schema Design for Wildframe OTT Platform

## Overview

This document describes the production-grade database schema for Wildframe, including:
- Entity-Relationship Diagrams
- Table definitions with constraints
- Indexing strategy for performance
- Partitioning strategy for scalability
- Migration strategy

## Database Architecture

### Database per Service Pattern

Each microservice owns its database to enforce loose coupling:

```
auth_db         → Auth Service (users, tokens, audit logs)
users_db        → User Service (profiles, devices, preferences)
content_db      → Content Service (movies, shows, genres)
streaming_db    → Streaming Service (playback sessions, watch history)
billing_db      → Billing Service (subscriptions, payments, invoices)
analytics_db    → Analytics Service (events, user behavior)
admin_db        → Admin Service (content management, moderation)
```

### Cross-Service Communication

Services communicate via:
1. **REST APIs** for synchronous requests
2. **Kafka Events** for asynchronous updates
3. **GraphQL** for complex queries (optional)

No direct database access between services.

## Core Schemas

### Auth Service (auth_db)

#### users
```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    email_verified BOOLEAN DEFAULT FALSE,
    email_verified_at TIMESTAMP,
    last_login_at TIMESTAMP,
    last_login_ip INET,
    login_attempts INTEGER DEFAULT 0,
    locked_until TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_email_active ON users(email, is_active);
CREATE INDEX idx_users_created_at ON users(created_at DESC);
```

#### refresh_tokens
```sql
CREATE TABLE refresh_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token VARCHAR(500) NOT NULL UNIQUE,
    device_id VARCHAR(255),
    ip_address INET,
    user_agent TEXT,
    expires_at TIMESTAMP NOT NULL,
    revoked_at TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_refresh_tokens_user_expires ON refresh_tokens(user_id, expires_at);
CREATE INDEX idx_refresh_tokens_device ON refresh_tokens(device_id, user_id);
CREATE INDEX idx_refresh_tokens_token ON refresh_tokens(token);
```

#### token_blacklist
```sql
CREATE TABLE token_blacklist (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    jti VARCHAR(500) NOT NULL UNIQUE,
    user_id UUID NOT NULL,
    revoked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_token_blacklist_jti ON token_blacklist(jti);
CREATE INDEX idx_token_blacklist_user_expires ON token_blacklist(user_id, expires_at);
```

#### login_audit
```sql
CREATE TABLE login_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID,
    email VARCHAR(255) NOT NULL,
    success BOOLEAN NOT NULL DEFAULT FALSE,
    ip_address INET,
    user_agent TEXT,
    failure_reason VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_login_audit_user_created ON login_audit(user_id, created_at DESC);
CREATE INDEX idx_login_audit_email_created ON login_audit(email, created_at DESC);
CREATE INDEX idx_login_audit_success ON login_audit(success, created_at DESC);
```

### User Service (users_db)

#### user_profiles
```sql
CREATE TABLE user_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE,
    bio TEXT,
    avatar_url VARCHAR(500),
    date_of_birth DATE,
    country_code CHAR(2),
    language CHAR(2) DEFAULT 'en',
    timezone VARCHAR(50),
    preferences JSONB DEFAULT '{}',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_user_profiles_user_id ON user_profiles(user_id);
CREATE INDEX idx_user_profiles_country ON user_profiles(country_code);
```

#### devices
```sql
CREATE TABLE devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES user_profiles(user_id) ON DELETE CASCADE,
    device_id VARCHAR(255) NOT NULL,
    device_name VARCHAR(255),
    device_type VARCHAR(50),
    os_type VARCHAR(50),
    os_version VARCHAR(50),
    app_version VARCHAR(50),
    last_seen TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, device_id)
);

CREATE INDEX idx_devices_user_id ON devices(user_id);
CREATE INDEX idx_devices_last_seen ON devices(last_seen DESC);
```

#### user_preferences
```sql
CREATE TABLE user_preferences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE REFERENCES user_profiles(user_id),
    default_quality VARCHAR(20) DEFAULT '1080p',
    autoplay_enabled BOOLEAN DEFAULT TRUE,
    subtitles_enabled BOOLEAN DEFAULT TRUE,
    subtitle_language CHAR(2),
    notification_email BOOLEAN DEFAULT TRUE,
    notification_push BOOLEAN DEFAULT TRUE,
    mature_content_enabled BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_user_preferences_user_id ON user_preferences(user_id);
```

### Content Service (content_db)

#### genres
```sql
CREATE TABLE genres (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,
    slug VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_genres_slug ON genres(slug);
CREATE INDEX idx_genres_name ON genres(name);
```

#### content
```sql
CREATE TABLE content (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(255) NOT NULL,
    description TEXT,
    release_date DATE NOT NULL,
    content_type VARCHAR(20) NOT NULL, -- 'movie' or 'show'
    rating_average NUMERIC(3,2) DEFAULT 0,
    rating_count INTEGER DEFAULT 0,
    runtime_minutes INTEGER,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_content_release_date ON content(release_date DESC);
CREATE INDEX idx_content_content_type ON content(content_type);
CREATE INDEX idx_content_rating ON content(rating_average DESC);
```

#### content_genres
```sql
CREATE TABLE content_genres (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_id UUID NOT NULL REFERENCES content(id) ON DELETE CASCADE,
    genre_id UUID NOT NULL REFERENCES genres(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (content_id, genre_id)
);

CREATE INDEX idx_content_genres_genre_id ON content_genres(genre_id);
```

#### episodes
```sql
CREATE TABLE episodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    show_id UUID NOT NULL REFERENCES content(id) ON DELETE CASCADE,
    season_number INTEGER NOT NULL,
    episode_number INTEGER NOT NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    duration_seconds INTEGER NOT NULL,
    air_date DATE,
    thumbnail_url VARCHAR(500),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(show_id, season_number, episode_number)
);

CREATE INDEX idx_episodes_show_season ON episodes(show_id, season_number);
CREATE INDEX idx_episodes_air_date ON episodes(air_date DESC);
```

#### video_files
```sql
CREATE TABLE video_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_id UUID NOT NULL REFERENCES content(id) ON DELETE CASCADE,
    episode_id UUID REFERENCES episodes(id) ON DELETE CASCADE,
    format VARCHAR(20) NOT NULL, -- 'hls' or 'dash'
    resolution VARCHAR(20) NOT NULL, -- '240p', '360p', '480p', '720p', '1080p', '4k'
    bitrate_kbps INTEGER NOT NULL,
    codec VARCHAR(50),
    duration_seconds INTEGER,
    file_size_bytes BIGINT,
    url VARCHAR(500) NOT NULL,
    manifest_url VARCHAR(500),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_video_files_content ON video_files(content_id, resolution);
CREATE INDEX idx_video_files_episode ON video_files(episode_id, resolution);
```

### Streaming Service (streaming_db)

#### playback_sessions
```sql
CREATE TABLE playback_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    device_id VARCHAR(255) NOT NULL,
    content_id UUID NOT NULL,
    episode_id UUID,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_position_seconds INTEGER DEFAULT 0,
    status VARCHAR(20) DEFAULT 'active',
    video_quality VARCHAR(20),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_playback_sessions_user ON playback_sessions(user_id, started_at DESC);
CREATE INDEX idx_playback_sessions_status ON playback_sessions(status);
PARTITION BY RANGE (created_at); -- Partition by month
```

#### watch_history (Time-series table - partitioned by month)
```sql
CREATE TABLE watch_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    content_id UUID NOT NULL,
    episode_id UUID,
    watched_at TIMESTAMP NOT NULL,
    duration_watched_seconds INTEGER,
    progress_percentage NUMERIC(5,2),
    completed BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) PARTITION BY RANGE (YEAR, MONTH(watched_at));

-- Partitions
CREATE TABLE watch_history_2026_05 PARTITION OF watch_history
    FOR VALUES FROM (2026, 5) TO (2026, 6);

CREATE INDEX idx_watch_history_user ON watch_history(user_id, watched_at DESC);
CREATE INDEX idx_watch_history_content ON watch_history(content_id);
CREATE INDEX idx_watch_history_completed ON watch_history(user_id, completed);
```

#### streaming_events (High-volume event table - partitioned by day)
```sql
CREATE TABLE streaming_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL,
    user_id UUID NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    event_data JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) PARTITION BY RANGE (created_at);

-- Daily partitions
CREATE TABLE streaming_events_2026_05_12 PARTITION OF streaming_events
    FOR VALUES FROM ('2026-05-12') TO ('2026-05-13');

CREATE INDEX idx_streaming_events_session ON streaming_events(session_id);
CREATE INDEX idx_streaming_events_type ON streaming_events(event_type);
```

### Billing Service (billing_db)

#### subscription_plans
```sql
CREATE TABLE subscription_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    price_usd NUMERIC(10,2) NOT NULL,
    billing_cycle VARCHAR(20) NOT NULL, -- 'monthly', 'annual'
    max_concurrent_streams INTEGER DEFAULT 1,
    max_resolution VARCHAR(20) DEFAULT '1080p',
    features JSONB DEFAULT '{}',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_subscription_plans_active ON subscription_plans(is_active);
```

#### user_subscriptions
```sql
CREATE TABLE user_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE,
    plan_id UUID NOT NULL REFERENCES subscription_plans(id),
    status VARCHAR(20) NOT NULL DEFAULT 'active', -- 'active', 'cancelled', 'suspended'
    current_period_start DATE NOT NULL,
    current_period_end DATE NOT NULL,
    auto_renew BOOLEAN DEFAULT TRUE,
    cancelled_at TIMESTAMP,
    reason_for_cancellation TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_user_subscriptions_user ON user_subscriptions(user_id);
CREATE INDEX idx_user_subscriptions_status ON user_subscriptions(status);
CREATE INDEX idx_user_subscriptions_end_date ON user_subscriptions(current_period_end);
```

#### invoices
```sql
CREATE TABLE invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    subscription_id UUID NOT NULL REFERENCES user_subscriptions(id),
    amount_usd NUMERIC(10,2) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    invoice_date DATE NOT NULL,
    due_date DATE,
    paid_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_invoices_user ON invoices(user_id, invoice_date DESC);
CREATE INDEX idx_invoices_status ON invoices(status);
```

#### payments
```sql
CREATE TABLE payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES invoices(id),
    provider VARCHAR(50) NOT NULL, -- 'stripe', 'paypal', etc.
    transaction_id VARCHAR(255) NOT NULL,
    amount_usd NUMERIC(10,2) NOT NULL,
    status VARCHAR(20) NOT NULL, -- 'pending', 'completed', 'failed'
    payment_method VARCHAR(50),
    error_message TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_payments_invoice ON payments(invoice_id);
CREATE INDEX idx_payments_status ON payments(status);
```

## Indexing Strategy

### B-Tree Indexes (Most Common)
```sql
-- Single column
CREATE INDEX idx_name ON table_name(column);

-- Composite (for queries using both columns)
CREATE INDEX idx_name ON table_name(col1, col2);

-- Covering index (includes non-key columns)
CREATE INDEX idx_name ON table_name(col1) INCLUDE (col2, col3);
```

### Partial Indexes (Space Efficient)
```sql
-- Only index active records
CREATE INDEX idx_users_active ON users(email) WHERE is_active = TRUE;

-- Only index recent data
CREATE INDEX idx_events_recent ON streaming_events(created_at DESC) 
WHERE created_at > NOW() - INTERVAL '30 days';
```

### GiST Indexes (Full-Text Search)
```sql
CREATE INDEX idx_content_search ON content 
USING GiST(to_tsvector('english', title || ' ' || description));
```

## Partitioning Strategy

### Time-Based Partitioning (for high-volume tables)
```sql
-- watch_history partitioned by month
CREATE TABLE watch_history (...)
PARTITION BY RANGE (YEAR, MONTH(watched_at));

-- streaming_events partitioned by day
CREATE TABLE streaming_events (...)
PARTITION BY RANGE (created_at);
```

### List Partitioning (for categorical data)
```sql
-- Partition content by type
CREATE TABLE content (...)
PARTITION BY LIST (content_type);

CREATE TABLE content_movies PARTITION OF content
FOR VALUES IN ('movie');

CREATE TABLE content_shows PARTITION OF content
FOR VALUES IN ('show');
```

## Query Optimization Tips

### 1. Use EXPLAIN ANALYZE
```sql
EXPLAIN ANALYZE
SELECT * FROM watch_history 
WHERE user_id = $1 AND watched_at > NOW() - INTERVAL '30 days'
ORDER BY watched_at DESC LIMIT 10;
```

### 2. Connection Pooling
Use PgBouncer for connection pooling:
```
max_client_conn = 1000
default_pool_size = 25
pool_mode = transaction
```

### 3. Statistics and VACUUM
```sql
ANALYZE content;
VACUUM ANALYZE;
```

## Disaster Recovery

### Backup Strategy
```bash
# Daily backups
pg_dump -Fc auth_db > /backups/auth_db_$(date +%Y%m%d).dump

# Point-in-time recovery setup
wal_level = replica
archive_mode = on
```

### Replication
```
# Streaming replication to standby nodes
primary_conninfo = 'host=primary_db'
standby_mode = 'on'
```

## Security

### Row-Level Security (RLS)
```sql
ALTER TABLE user_subscriptions ENABLE ROW LEVEL SECURITY;

CREATE POLICY user_sees_own_subscriptions ON user_subscriptions
    FOR SELECT USING (user_id = CURRENT_USER_ID);
```

### Encryption
- Encryption at rest: PostgreSQL with pgcrypto
- Encryption in transit: SSL/TLS connections
- Sensitive fields: AES-256 encryption

---

This schema design prioritizes:
- ✅ Scalability through partitioning
- ✅ Performance through strategic indexing
- ✅ Security through RLS and encryption
- ✅ Flexibility through JSONB fields
- ✅ Maintainability through clear relationships
- ✅ Consistency through constraints
