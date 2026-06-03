# 📊 Database Schema Reference

**Version**: 2.0.0  
**Last Updated**: May 27, 2026  

## Overview

Wildframe uses PostgreSQL with a **database-per-service** pattern. Each service owns its data independently, allowing horizontal scaling and service isolation.

**Read Time**: 15 minutes  
**Prerequisites**: Basic SQL and relational database knowledge

## Table of Contents

1. [Database Overview](#database-overview)
2. [Service Databases](#service-databases)
3. [Shared Reference Data](#shared-reference-data)
4. [Data Flow Between Services](#data-flow-between-services)
5. [Backup Strategy](#backup-strategy)

---

## Database Overview

### 12 Independent Databases

```
PostgreSQL Server (Port 5432)
├── auth_db              [2 tables]
├── users_db             [6 tables]
├── content_db           [6 tables]
├── streaming_db         [4 tables]
├── search_db            [2 tables]
├── recommendation_db    [3 tables]
├── billing_db           [4 tables]
├── analytics_db         [3 tables]
├── notification_db      [3 tables]
├── media_db             [2 tables]
├── admin_db             [4 tables]
└── wildframe_db         [Master reference]
```

### Connection String Format

```
postgresql://username:password@localhost:5432/auth_db
postgresql://auth_user:auth_password@postgres:5432/auth_db
```

---

## Service Databases

### 1. Auth Database (`auth_db`)

Purpose: Authentication and JWT token management

#### `users_auth` Table

```sql
CREATE TABLE users_auth (
    id UUID PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    email_verified BOOLEAN DEFAULT FALSE,
    email_verified_at TIMESTAMP WITH TIME ZONE,
    mfa_enabled BOOLEAN DEFAULT FALSE,
    mfa_secret VARCHAR(255),
    failed_login_attempts INT DEFAULT 0,
    last_failed_login TIMESTAMP WITH TIME ZONE,
    account_locked BOOLEAN DEFAULT FALSE,
    account_locked_until TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_email (email),
    INDEX idx_email_verified (email_verified),
    INDEX idx_account_locked (account_locked)
);
```

**Key Fields**:
- `id`: UUID unique identifier across entire platform
- `email`: Unique email (used for login)
- `password_hash`: bcrypt hashed password (never plaintext)
- `email_verified`: Email verification status
- `mfa_enabled`: Multi-factor authentication enabled
- `account_locked`: Temporary lockout after 5 failed attempts

#### `refresh_tokens` Table

```sql
CREATE TABLE refresh_tokens (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users_auth(id) ON DELETE CASCADE,
    token_hash VARCHAR(255) NOT NULL UNIQUE,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    revoked BOOLEAN DEFAULT FALSE,
    revoked_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_user_id (user_id),
    INDEX idx_expires_at (expires_at)
);
```

**Key Fields**:
- `token_hash`: SHA256 hash of refresh token (not stored plaintext)
- `expires_at`: Token expiration (typically 7 days)
- `revoked`: Can be revoked before expiration
- `revoked_at`: When token was revoked (for audit)

**Relationships**:
- `refresh_tokens.user_id` → `users_auth.id` (CASCADE delete)

---

### 2. Users Database (`users_db`)

Purpose: User profiles, devices, preferences, watch history

#### `user_profiles` Table

```sql
CREATE TABLE user_profiles (
    id UUID PRIMARY KEY,
    auth_id UUID NOT NULL UNIQUE,  -- Links to auth_db.users_auth.id
    username VARCHAR(100) NOT NULL UNIQUE,
    first_name VARCHAR(50),
    last_name VARCHAR(50),
    bio TEXT,
    avatar_url VARCHAR(500),
    date_of_birth DATE,
    country VARCHAR(2),
    language VARCHAR(5) DEFAULT 'en',
    timezone VARCHAR(50) DEFAULT 'UTC',
    preferences_json JSONB,
    account_status ENUM('active', 'suspended', 'deleted') DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP WITH TIME ZONE,
    
    INDEX idx_username (username),
    INDEX idx_account_status (account_status),
    INDEX idx_created_at (created_at)
);
```

#### `devices` Table

```sql
CREATE TABLE devices (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    device_name VARCHAR(100) NOT NULL,
    device_type ENUM('phone', 'tablet', 'desktop', 'tv') NOT NULL,
    device_os VARCHAR(50),
    device_model VARCHAR(100),
    app_version VARCHAR(20),
    last_active_at TIMESTAMP WITH TIME ZONE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_user_id (user_id),
    INDEX idx_device_type (device_type),
    INDEX idx_last_active_at (last_active_at)
);
```

#### `user_sessions` Table

```sql
CREATE TABLE user_sessions (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    device_id UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    access_token_hash VARCHAR(255) NOT NULL UNIQUE,
    refresh_token_hash VARCHAR(255) NOT NULL UNIQUE,
    ip_address VARCHAR(45),
    user_agent TEXT,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_activity_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_user_id (user_id),
    INDEX idx_device_id (device_id),
    INDEX idx_expires_at (expires_at),
    INDEX idx_access_token_hash (access_token_hash)
);
```

#### `watch_history` Table

```sql
CREATE TABLE watch_history (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    content_id VARCHAR(100) NOT NULL,  -- References content_db.movies/shows
    content_type ENUM('movie', 'show', 'episode') NOT NULL,
    progress_percentage FLOAT DEFAULT 0,
    watched_duration_seconds INT,
    total_duration_seconds INT,
    watched_at TIMESTAMP WITH TIME ZONE NOT NULL,
    
    INDEX idx_user_id (user_id),
    INDEX idx_content_id (content_id),
    INDEX idx_watched_at (watched_at),
    UNIQUE (user_id, content_id)  -- One record per user+content
);
```

#### `user_preferences` Table

```sql
CREATE TABLE user_preferences (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL UNIQUE REFERENCES user_profiles(id) ON DELETE CASCADE,
    language VARCHAR(5) DEFAULT 'en',
    subtitle_enabled BOOLEAN DEFAULT TRUE,
    subtitle_language VARCHAR(5) DEFAULT 'en',
    autoplay_next BOOLEAN DEFAULT TRUE,
    quality_preference ENUM('auto', '480p', '720p', '1080p', '4k') DEFAULT 'auto',
    notification_enabled BOOLEAN DEFAULT TRUE,
    email_notifications BOOLEAN DEFAULT TRUE,
    push_notifications BOOLEAN DEFAULT TRUE,
    marketing_emails BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_user_id (user_id)
);
```

**Relationships**:
- `devices.user_id` → `user_profiles.id`
- `user_sessions.user_id` → `user_profiles.id`
- `user_sessions.device_id` → `devices.id`
- `watch_history.user_id` → `user_profiles.id`
- `user_preferences.user_id` → `user_profiles.id`

---

### 3. Content Database (`content_db`)

Purpose: Movie and show catalog

#### `genres` Table

```sql
CREATE TABLE genres (
    id UUID PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,
    description TEXT,
    slug VARCHAR(50) NOT NULL UNIQUE,
    icon_url VARCHAR(500),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_name (name),
    INDEX idx_slug (slug)
);
```

#### `movies` Table

```sql
CREATE TABLE movies (
    id UUID PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    slug VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    release_date DATE,
    duration_minutes INT,
    rating DECIMAL(3, 1),
    rating_count INT DEFAULT 0,
    director VARCHAR(100),
    cast TEXT,
    language VARCHAR(5),
    countries TEXT,
    genres_json JSONB,
    poster_url VARCHAR(500),
    backdrop_url VARCHAR(500),
    trailer_url VARCHAR(500),
    content_rating ENUM('G', 'PG', 'PG-13', 'R', 'NC-17') DEFAULT 'PG-13',
    is_featured BOOLEAN DEFAULT FALSE,
    is_available BOOLEAN DEFAULT TRUE,
    availability_regions TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_title (title),
    INDEX idx_slug (slug),
    INDEX idx_rating (rating),
    INDEX idx_is_featured (is_featured),
    INDEX idx_release_date (release_date)
);
```

#### `shows` Table

```sql
CREATE TABLE shows (
    id UUID PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    slug VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    first_aired DATE,
    last_aired DATE,
    season_count INT DEFAULT 0,
    episode_count INT DEFAULT 0,
    rating DECIMAL(3, 1),
    rating_count INT DEFAULT 0,
    creator VARCHAR(100),
    cast TEXT,
    language VARCHAR(5),
    countries TEXT,
    genres_json JSONB,
    poster_url VARCHAR(500),
    backdrop_url VARCHAR(500),
    content_rating ENUM('G', 'PG', 'PG-13', 'R', 'NC-17') DEFAULT 'PG-13',
    is_featured BOOLEAN DEFAULT FALSE,
    is_available BOOLEAN DEFAULT TRUE,
    availability_regions TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_title (title),
    INDEX idx_slug (slug),
    INDEX idx_rating (rating),
    INDEX idx_is_featured (is_featured)
);
```

#### `seasons` Table

```sql
CREATE TABLE seasons (
    id UUID PRIMARY KEY,
    show_id UUID NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
    season_number INT NOT NULL,
    title VARCHAR(255),
    description TEXT,
    episode_count INT DEFAULT 0,
    air_date DATE,
    poster_url VARCHAR(500),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_show_id (show_id),
    INDEX idx_season_number (season_number),
    UNIQUE (show_id, season_number)
);
```

#### `episodes` Table

```sql
CREATE TABLE episodes (
    id UUID PRIMARY KEY,
    show_id UUID NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
    season_id UUID NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    season_number INT NOT NULL,
    episode_number INT NOT NULL,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    duration_minutes INT,
    air_date DATE,
    rating DECIMAL(3, 1),
    rating_count INT DEFAULT 0,
    thumbnail_url VARCHAR(500),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_show_id (show_id),
    INDEX idx_season_id (season_id),
    INDEX idx_air_date (air_date),
    UNIQUE (show_id, season_number, episode_number)
);
```

**Relationships**:
- `movies.genres_json` and `shows.genres_json` reference `genres` by ID (JSONB array)
- `seasons.show_id` → `shows.id`
- `episodes.show_id` → `shows.id`
- `episodes.season_id` → `seasons.id`

---

### 4. Streaming Database (`streaming_db`)

Purpose: Video streaming and playback state

#### `playback_sessions` Table

```sql
CREATE TABLE playback_sessions (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,  -- References users_db.user_profiles.id
    content_id VARCHAR(100) NOT NULL,
    content_type ENUM('movie', 'show', 'episode') NOT NULL,
    device_id UUID NOT NULL,  -- References users_db.devices.id
    server_id VARCHAR(50),
    rtmp_url VARCHAR(500),
    hls_url VARCHAR(500),
    bitrate_kbps INT DEFAULT 5000,
    quality ENUM('480p', '720p', '1080p', '4k') DEFAULT '1080p',
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP WITH TIME ZONE,
    is_active BOOLEAN DEFAULT TRUE,
    
    INDEX idx_user_id (user_id),
    INDEX idx_content_id (content_id),
    INDEX idx_started_at (started_at)
);
```

#### `video_segments` Table

```sql
CREATE TABLE video_segments (
    id UUID PRIMARY KEY,
    playback_session_id UUID NOT NULL REFERENCES playback_sessions(id) ON DELETE CASCADE,
    segment_number INT NOT NULL,
    segment_url VARCHAR(500) NOT NULL,
    duration_seconds INT,
    bandwidth_kbps INT,
    codec VARCHAR(50),
    resolution VARCHAR(20),
    downloaded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_playback_session_id (playback_session_id),
    INDEX idx_segment_number (segment_number)
);
```

#### `streaming_quality_metrics` Table

```sql
CREATE TABLE streaming_quality_metrics (
    id UUID PRIMARY KEY,
    playback_session_id UUID NOT NULL REFERENCES playback_sessions(id) ON DELETE CASCADE,
    average_bitrate_kbps INT,
    buffering_count INT DEFAULT 0,
    total_buffering_seconds INT DEFAULT 0,
    packet_loss_percentage FLOAT DEFAULT 0,
    latency_ms INT DEFAULT 0,
    quality_score FLOAT,  -- 0-100, calculated from metrics
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_playback_session_id (playback_session_id)
);
```

---

### 5. Search Database (`search_db`)

Purpose: Full-text search indexes

#### `content_search_index` Table

```sql
CREATE TABLE content_search_index (
    id UUID PRIMARY KEY,
    content_id VARCHAR(100) NOT NULL,
    content_type ENUM('movie', 'show') NOT NULL,
    title VARCHAR(255),
    description TEXT,
    genres TEXT,
    cast TEXT,
    director VARCHAR(100),
    tsvector tsvector,  -- PostgreSQL full-text search vector
    indexed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_content_id (content_id),
    INDEX idx_tsvector (tsvector USING gin)  -- GIN index for FTS
);
```

---

### 6. Recommendation Database (`recommendation_db`)

Purpose: ML model data and recommendations

#### `user_preferences_ml` Table

```sql
CREATE TABLE user_preferences_ml (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL UNIQUE,  -- References users_db.user_profiles.id
    genre_scores JSONB,  -- {"action": 0.8, "drama": 0.5, ...}
    actor_preferences JSONB,
    director_preferences JSONB,
    content_type_preferences JSONB,
    rating_threshold FLOAT DEFAULT 6.0,
    recently_watched_content JSONB,  -- Last 50 content IDs
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_user_id (user_id)
);
```

#### `recommendations` Table

```sql
CREATE TABLE recommendations (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES user_preferences_ml(user_id) ON DELETE CASCADE,
    content_id VARCHAR(100) NOT NULL,
    content_type ENUM('movie', 'show') NOT NULL,
    score FLOAT,  -- 0-100 confidence score
    reason VARCHAR(255),  -- "Based on watched action movies"
    rank INT,  -- 1, 2, 3, ... (order in recommendations)
    generated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    viewed_at TIMESTAMP WITH TIME ZONE,
    
    INDEX idx_user_id (user_id),
    INDEX idx_generated_at (generated_at),
    INDEX idx_rank (rank)
);
```

---

### 7. Billing Database (`billing_db`)

Purpose: Subscription and payment management

#### `subscriptions` Table

```sql
CREATE TABLE subscriptions (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL UNIQUE,  -- References users_db.user_profiles.id
    plan ENUM('free', 'basic', 'premium', 'family') NOT NULL DEFAULT 'free',
    status ENUM('active', 'cancelled', 'suspended', 'expired') DEFAULT 'active',
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    renews_at TIMESTAMP WITH TIME ZONE,
    cancelled_at TIMESTAMP WITH TIME ZONE,
    cancellation_reason TEXT,
    stripe_subscription_id VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_user_id (user_id),
    INDEX idx_plan (plan),
    INDEX idx_status (status),
    INDEX idx_renews_at (renews_at)
);
```

#### `payments` Table

```sql
CREATE TABLE payments (
    id UUID PRIMARY KEY,
    subscription_id UUID NOT NULL REFERENCES subscriptions(id),
    amount_cents INT NOT NULL,  -- In cents ($10.99 = 1099)
    currency VARCHAR(3) DEFAULT 'USD',
    stripe_payment_id VARCHAR(255) NOT NULL UNIQUE,
    payment_method VARCHAR(50),  -- 'card', 'paypal', 'apple_pay'
    status ENUM('pending', 'completed', 'failed', 'refunded') DEFAULT 'pending',
    receipt_url VARCHAR(500),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP WITH TIME ZONE,
    
    INDEX idx_subscription_id (subscription_id),
    INDEX idx_status (status),
    INDEX idx_created_at (created_at)
);
```

#### `invoices` Table

```sql
CREATE TABLE invoices (
    id UUID PRIMARY KEY,
    subscription_id UUID NOT NULL REFERENCES subscriptions(id),
    invoice_number VARCHAR(50) NOT NULL UNIQUE,
    amount_cents INT NOT NULL,
    due_date DATE,
    pdf_url VARCHAR(500),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_subscription_id (subscription_id),
    INDEX idx_invoice_number (invoice_number)
);
```

---

## Shared Reference Data

### Cross-Database References

Services reference each other's data via **IDs only** (not foreign keys):

```
Example: Watch History
┌─────────────────────────────┐
│  users_db                   │
│  watch_history              │
│  ├─ user_id → users.id ✅  │
│  └─ content_id → "mov_123"  │
│      (references content_db │
│       without FK constraint) │
└─────────────────────────────┘
         ↓
┌─────────────────────────────┐
│  content_db                 │
│  movies                     │
│  └─ id = "mov_123" ✅      │
└─────────────────────────────┘
```

**Why not foreign keys?**
- Improves scalability
- Allows services to scale independently
- Avoids cross-database constraints
- Maintains data consistency via application logic

---

## Data Flow Between Services

### Typical Request Flow

```
1. User Registration
   ├─ auth-service: INSERT into users_auth ✓
   └─ user-service: INSERT into user_profiles (async event)

2. Watch Movie
   ├─ auth-service: Validate token ✓
   ├─ streaming-service: Create playback_session ✓
   ├─ user-service: UPDATE watch_history (async) ✓
   └─ recommendation-service: Update user preferences (async)

3. Get Recommendations
   ├─ auth-service: Validate token ✓
   ├─ recommendation-service: Query recommendations table ✓
   └─ content-service: Enrich with movie details ✓
```

### Event-Driven Updates

Services communicate via **Kafka events**, not direct database connections:

```
User watches movie:
    ┌──────────────────┐
    │ streaming-service│
    │ (publishes event)│
    └────────┬─────────┘
             │ 
        KAFKA TOPIC
     "video.watched"
             │
    ┌────────▼──────────┐
    │ user-service      │
    │ (subscribes)      │
    ├──────────────────┐│
    │ Updates:         ││
    │ • watch_history  ││
    │ • last_watched   ││
    └──────────────────┘│
                        │
    ┌────────▼──────────┐
    │ analytics-service │
    │ (subscribes)      │
    ├──────────────────┐│
    │ Logs:            ││
    │ • viewing_time   ││
    │ • completion %   ││
    └──────────────────┘│
```

---

## Backup Strategy

### Backup Schedule

```
Full Backups (Daily)
├─ Time: 02:00 UTC
├─ Retention: 30 days
└─ Location: S3 with cross-region replication

Incremental Backups (Every 6 hours)
├─ Times: 00:00, 06:00, 12:00, 18:00 UTC
├─ Retention: 7 days
└─ Location: S3 regional

Transaction Logs (Continuous)
├─ Archive every 30 seconds
├─ Retention: 30 days
└─ Location: S3 with immutable version lock
```

### Backup Verification

```bash
# Weekly: Test restore on staging database
# Monthly: Full restore to verify data integrity
# Post-Incident: Verify backup captures all recent changes
```

### RTO/RPO Targets

| Scenario | RTO | RPO |
|----------|-----|-----|
| Single table corruption | 1 hour | 1 minute |
| Full database loss | 4 hours | 6 hours |
| Entire region down | 24 hours | 1 hour |

---

## Migration Strategy

### Adding a New Table

```sql
-- 1. Create table with new columns
ALTER TABLE movies ADD COLUMN popularity_score FLOAT;

-- 2. Backfill existing data
UPDATE movies SET popularity_score = 0.5 WHERE popularity_score IS NULL;

-- 3. Add NOT NULL constraint
ALTER TABLE movies ALTER COLUMN popularity_score SET NOT NULL;

-- 4. Create indexes if needed
CREATE INDEX idx_popularity ON movies(popularity_score);
```

### Backward Compatibility

- New columns must have defaults
- Old code must ignore new columns
- Careful with NOT NULL constraints
- Deploy code changes before schema changes

---

## Performance Considerations

### Query Optimization

```sql
-- ❌ Slow: No index
SELECT * FROM movies WHERE title = 'Inception';

-- ✅ Fast: Uses index
SELECT * FROM movies WHERE title = 'Inception' AND is_available = TRUE;

-- ❌ Slow: Full table scan
SELECT * FROM watch_history WHERE watched_at > NOW() - INTERVAL '30 days';

-- ✅ Fast: Uses index
SELECT * FROM watch_history 
WHERE user_id = '...' AND watched_at > NOW() - INTERVAL '30 days';
```

### Caching Strategy

```
Database Tier
    ↓ (caches results for 1 hour)
Redis Tier
    ↓ (cold miss)
Application Tier
    ↓ (queries)
PostgreSQL
```

### Connection Pooling

```
Each Service
├─ PgBouncer (connection pool)
│  └─ 20 connections to PostgreSQL
├─ Timeout: 10 minutes idle
└─ Max clients: 1000
```

---

## Monitoring & Alerts

### Key Metrics

```
Per Service Database:
├─ Connection count (alert if > 90% max)
├─ Query performance (p99 latency)
├─ Disk usage (alert if > 80%)
├─ Replication lag (alert if > 1 second)
└─ Failed transactions (alert if > 0)
```

### Query Logs

```
Enable slow query logging:
log_min_duration_statement = 1000  -- 1 second in ms

Files monitored by Loki (log aggregation):
/var/log/postgresql/postgresql.log
```

---

## Resources

- [PostgreSQL Docs](https://www.postgresql.org/docs/)
- [Database Design Guide](../docs/DATABASE_DESIGN.md)
- [API Documentation](../docs/API_DOCUMENTATION.md)
- [Backup Procedures](../docs/OPERATIONS_GUIDE.md#backups)

---

**Last Verified**: May 27, 2026  
**Next Review**: June 27, 2026
