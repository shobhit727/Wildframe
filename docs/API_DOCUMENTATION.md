# 📋 API Documentation

**Version**: 0.9 — **inaccurate, see below**  
**Last Updated**: August 9, 2026 (content); banner added 2026-10-14  
**Stability**: **not** production-ready as a reference

> ## ⚠️ This file previously claimed to be a complete API reference. It is not.
>
> The earlier header said *"Complete API reference for all Wildframe
> microservices… documents every endpoint"* and was marked
> **Production-Ready**. Both were false, and the errors are the kind that cost
> an integrator a day:
>
> - **14 of the 19 endpoints it documents return 404.** Verified 2026-10-14 by
>   generating `openapi.json` from all 15 services and resolving every
>   documented method+path against it.
> - **It covers 4 of 15 services.** Nothing for search, admin,
>   recommendation, billing, analytics, notification, media-pipeline, creators,
>   moderation or uploads.
> - **It omits every state-changing route** — `POST
>   /api/v1/playback-sessions`, `POST /api/v1/purchases`, the moderation
>   decisions, the creator onboarding, the admin actions.
> - **Its pagination envelope is invented.** `has_next` / `has_prev` appear in
>   no schema in any service. No service returns that shape.
> - **Its `/search` and `/movies` paths belong to no service.** `search-service`
>   serves `/api/v1/genres`, `/api/v1/content` and `/api/v1/reindex`;
>   `content-service` serves `/api/v1/content` and `/api/v1/content/{id}`.
>   Search is Elasticsearch, reached through `content-service`.
>
> ### The only trustworthy inventory is generated, not written
>
> ```bash
> curl -sk https://localhost:8001/openapi.json   # auth-service
> curl -sk https://localhost:8002/openapi.json   # user-service
> curl -sk https://localhost:8003/openapi.json   # content-service
> # 8000 is the api-gateway proxy: it exposes /health, /ready, /metrics,
> # /gateway/health, /gateway/ready, /gateway/services, and a catch-all
> # /{service}/... that forwards. It owns no business route.
> ```
>
> Host port per service is in `infrastructure/caddy/Caddyfile`. The
> authoritative list is what those documents contain; this file is a
> hand-written sketch of 4 of them.
>
> **The sections below that carry `❌ 404` markers are wrong and are kept
> only so the diff against the old file is reviewable. Do not code against
> them.** The auth-service section is correct.

## Overview

A hand-written sketch of the auth, user, content and streaming APIs. It is
**not** a reference. Read it for the auth flow and the JWT claims; get
everything else from `openapi.json`.


## Quick Links

- [Base URLs](#base-urls)
- [Authentication](#authentication)
- [Status Codes](#status-codes)
- [Services](#services)

---

## Base URLs

| Environment | URL |
|-------------|-----|
| **Development** | `https://localhost:8000` |
| **Staging** | `https://staging-api.wildframe.com` |
| **Production** | `https://api.wildframe.com` |

---

## Authentication

### JWT Token Flow

```
1. User calls POST /auth/login with credentials
2. Without MFA: service returns { access_token, refresh_token }
   With MFA enabled: service returns 200 { requires_mfa: true, mfa_challenge, expires_in }
3. MFA login: POST /auth/mfa/login-verify with { mfa_challenge, code } → tokens
4. Client includes access_token in Authorization header:
   Authorization: Bearer <access_token>
5. When token expires, use refresh_token to get new one
6. Invalid/expired tokens return 401 Unauthorized
```

### Token Headers

All authenticated endpoints require:

```http
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

Token lifetime (auth-service defaults):
- **Access Token**: 15 minutes
- **Refresh Token**: 7 days
- **MFA challenge**: 5 minutes
- **Token Blacklist**: tokens remain blacklisted after logout
- **Email verification**: signed JWT of type `email_verification` issued at registration; POST `/auth/verify-email` with the token

> Note: routes are mounted under `/api/v1` on each service and reachable
> through the gateway at `/{service}/api/v1/...` (e.g.
> `https://localhost:8000/auth/api/v1/auth/login`).

> Note: auth-issued access tokens carry the audience claim
> `aud: "wildframe-api"`. Every backend service that verifies them decodes
> with `audience="wildframe-api"` (from `settings.JWT_AUDIENCE`); a decode
> that omits the audience raises `JWTClaimsError: Invalid audience`. The
> api-gateway is a transparent proxy — it rate-limits proxied requests but
> does not reject them itself; each service enforces its own auth boundary.

---

## Rate Limiting

The api-gateway enforces per-client rate limits on proxied requests:

- **Key**: authenticated user `sub` (JWT) when present, otherwise the client IP.
- **Behavior**: when the limit is exceeded the gateway returns:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: <seconds>
Content-Type: application/json

{"detail": "Rate limit exceeded"}
```

- Auth endpoints additionally rate-limit failed login attempts (5 per 5 min
  per account/IP).

---

## Status Codes

| Code | Meaning | Retry? |
|------|---------|--------|
| **200** | OK - Request succeeded | No |
| **201** | Created - Resource created | No |
| **204** | No Content - Success with no body | No |
| **400** | Bad Request - Invalid parameters | No |
| **401** | Unauthorized - Missing/invalid token | Yes (after refresh) |
| **403** | Forbidden - User lacks permission | No |
| **404** | Not Found - Resource doesn't exist | No |
| **409** | Conflict - Resource already exists | No |
| **422** | Unprocessable Entity - Validation failed | No |
| **429** | Too Many Requests - Rate limited | Yes (exponential backoff) |
| **500** | Server Error - Internal error | Yes (exponential backoff) |
| **503** | Service Unavailable - Maintenance/down | Yes (after delay) |

---

## Services

### 1. Auth Service (Host port 8001)

#### Register User

```http
POST /auth/register
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "SecurePass123!",
  "first_name": "John",
  "last_name": "Doe"
}
```

**Response** (201 Created):
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "email": "user@example.com",
  "first_name": "John",
  "last_name": "Doe",
  "created_at": "2026-05-28T10:30:00Z",
  "email_verification_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Errors**:
- `400`: Password too weak (< 8 chars, no uppercase/number)
- `409`: Email already registered

#### Verify Email

```http
POST /auth/verify-email
Content-Type: application/json

{"token": "<email_verification_token>"}
```

**Response** (200 OK): `{"message": "Email verified successfully"}`
**Errors**: `400` invalid or expired verification token.

#### Login

```http
POST /auth/login
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "SecurePass123!"
}
```

**Response** (200 OK):
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 900
}
```

**MFA-enabled account** (200 OK, no tokens yet):
```json
{
  "requires_mfa": true,
  "mfa_challenge": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expires_in": 300
}
```
Then complete with `POST /auth/mfa/login-verify` `{"mfa_challenge": "...", "code": "123456"}` → same token response as above.

**Errors**:
- `401`: Invalid email or password
- `429`: Too many failed attempts (rate limited 5 attempts/5min)

#### Refresh Token

```http
POST /auth/refresh
Content-Type: application/json

{
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Response** (200 OK):
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expires_in": 3600
}
```

**Errors**:
- `401`: Invalid or expired refresh token

#### Logout

```http
POST /auth/logout
Authorization: Bearer <access_token>
```

**Response** (204 No Content):
```
(empty body)
```

---

### 2. User Service (Host port 8002)

#### Get Current User Profile

```http
GET /users/me
Authorization: Bearer <access_token>
```

**Response** (200 OK):
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "email": "user@example.com",
  "first_name": "John",
  "last_name": "Doe",
  "avatar_url": "https://cdn.wildframe.com/avatars/123e4567.jpg",
  "subscription_plan": "premium",
  "created_at": "2026-05-28T10:30:00Z"
}
```

#### Update User Profile

```http
PUT /users/me
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "first_name": "Jonathan",
  "avatar_url": "https://cdn.wildframe.com/avatars/new.jpg"
}
```

**Response** (200 OK):
```json
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "email": "user@example.com",
  "first_name": "Jonathan",
  "last_name": "Doe",
  "updated_at": "2026-05-28T12:00:00Z"
}
```

#### List User Devices

```http
GET /users/devices
Authorization: Bearer <access_token>
```

**Response** (200 OK):
```json
{
  "devices": [
    {
      "id": "d1a2b3c4-5678-90ab-cdef-1234567890ab",
      "name": "iPhone 14",
      "type": "mobile",
      "os": "iOS 17",
      "is_active": true,
      "last_used": "2026-05-28T15:30:00Z"
    },
    {
      "id": "d2a2b3c4-5678-90ab-cdef-1234567890ab",
      "name": "Samsung TV",
      "type": "tv",
      "os": "Tizen 7.0",
      "is_active": false,
      "last_used": "2026-05-27T20:00:00Z"
    }
  ]
}
```

#### Remove Device

```http
DELETE /users/devices/{device_id}
Authorization: Bearer <access_token>
```

**Response** (204 No Content):
```
(empty body)
```

#### Get Watch History

```http
GET /users/watch-history?limit=10&offset=0
Authorization: Bearer <access_token>
```

**Response** (200 OK):
```json
{
  "items": [
    {
      "content_id": "c1a2b3c4-5678-90ab-cdef-1234567890ab",
      "content_title": "Stranger Things",
      "content_type": "show",
      "watch_duration_seconds": 2400,
      "total_duration_seconds": 2700,
      "watched_at": "2026-05-28T20:00:00Z"
    }
  ],
  "total": 156,
  "limit": 10,
  "offset": 0
}
```

#### Update User Preferences

```http
PUT /users/preferences
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "auto_play_next": true,
  "preferred_language": "en",
  "preferred_subtitle": "en",
  "video_quality": "1080p"
}
```

**Response** (200 OK):
```json
{
  "auto_play_next": true,
  "preferred_language": "en",
  "preferred_subtitle": "en",
  "video_quality": "1080p",
  "updated_at": "2026-05-28T15:00:00Z"
}
```

---

### 3. Content Service (Host port 8003)

> Note: creating a genre that already exists returns `409 Conflict`
> (not a 500).

#### List Movies

```http
GET /movies?genre=action&limit=20&offset=0&sort=-release_date
Authorization: Bearer <access_token>
```

**Query Parameters**:
- `genre`: Filter by genre (optional)
- `sort`: Sort field, prefix with `-` for descending (optional, default: `-created_at`)
- `limit`: Results per page (optional, default: 20, max: 100)
- `offset`: Pagination offset (optional, default: 0)

**Response** (200 OK):
```json
{
  "items": [
    {
      "id": "m1a2b3c4-5678-90ab-cdef-1234567890ab",
      "title": "The Matrix",
      "description": "A computer programmer discovers...",
      "release_date": "1999-03-31",
      "duration_minutes": 136,
      "rating": 8.7,
      "genres": ["action", "sci-fi"],
      "thumbnail_url": "https://cdn.wildframe.com/thumbnails/matrix.jpg"
    }
  ],
  "total": 1250,
  "limit": 20,
  "offset": 0
}
```

#### Get Movie Details

```http
GET /movies/{movie_id}
Authorization: Bearer <access_token>
```

**Response** (200 OK):
```json
{
  "id": "m1a2b3c4-5678-90ab-cdef-1234567890ab",
  "title": "The Matrix",
  "description": "A computer programmer discovers...",
  "release_date": "1999-03-31",
  "duration_minutes": 136,
  "rating": 8.7,
  "vote_count": 5000,
  "genres": ["action", "sci-fi"],
  "cast": ["Keanu Reeves", "Laurence Fishburne"],
  "director": "Lana Wachowski, Lilly Wachowski",
  "poster_url": "https://cdn.wildframe.com/posters/matrix.jpg",
  "backdrop_url": "https://cdn.wildframe.com/backdrops/matrix.jpg",
  "thumbnail_url": "https://cdn.wildframe.com/thumbnails/matrix.jpg"
}
```

#### Search Content

```http
GET /search?q=batman&content_type=movie&limit=10
Authorization: Bearer <access_token>
```

**Response** (200 OK):
```json
{
  "results": [
    {
      "id": "c1a2b3c4-5678-90ab-cdef-1234567890ab",
      "title": "The Dark Knight",
      "type": "movie",
      "rating": 9.0,
      "thumbnail_url": "https://cdn.wildframe.com/thumbnails/darkknight.jpg"
    },
    {
      "id": "c2a2b3c4-5678-90ab-cdef-1234567890ab",
      "title": "Batman Begins",
      "type": "movie",
      "rating": 8.3,
      "thumbnail_url": "https://cdn.wildframe.com/thumbnails/batmanbegins.jpg"
    }
  ],
  "total": 156
}
```

---

### 4. Streaming Service (Host port 8004)

> 🔒 **Auth (Aug 9, 2026)**: every streaming endpoint requires a Bearer token —
> playback sessions, manifests, transcoding jobs, quality profiles, CDN
> regions, and download sessions. Without a token → `401`. User-scoped reads
> (`/users/{user_id}/playback-sessions`, `/users/{user_id}/downloads`) are
> owner-only → `403` for other users. Session `user_id` in request bodies is
> ignored — the JWT claim always wins. `POST /playback-sessions/{id}/end`
> returns `403` to non-owners without touching the session.

#### Start Streaming Session

```http
POST /streaming/sessions
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "content_id": "m1a2b3c4-5678-90ab-cdef-1234567890ab",
  "device_id": "d1a2b3c4-5678-90ab-cdef-1234567890ab",
  "resume_position_seconds": 0
}
```

**Response** (201 Created):
```json
{
  "session_id": "s1a2b3c4-5678-90ab-cdef-1234567890ab",
  "manifest_url": "https://cdn.wildframe.com/manifests/m1a2b3c4.m3u8",
  "bitrate_options": [480, 720, 1080, 2160],
  "resume_position_seconds": 0,
  "duration_seconds": 8160
}
```

#### Get Manifest (HLS/DASH)

```http
GET /streaming/manifests/{content_id}?bitrate=1080
Authorization: Bearer <access_token>
```

**Response** (200 OK):
```
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXTINF:10.0,
segment-0000.ts
#EXTINF:10.0,
segment-0001.ts
...
#EXT-X-ENDLIST
```

#### Update Watch Position

```http
PUT /streaming/sessions/{session_id}
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "position_seconds": 1200,
  "bitrate_used": 1080
}
```

**Response** (200 OK):
```json
{
  "session_id": "s1a2b3c4-5678-90ab-cdef-1234567890ab",
  "position_seconds": 1200,
  "updated_at": "2026-05-28T15:30:00Z"
}
```

#### End Streaming Session

```http
DELETE /streaming/sessions/{session_id}
Authorization: Bearer <access_token>
```

**Response** (204 No Content):
```
(empty body)
```

---

## Error Response Format

All error responses follow this format:

```json
{
  "error": {
    "code": "INVALID_EMAIL",
    "message": "Email format is invalid",
    "details": {
      "field": "email",
      "constraint": "email_format"
    }
  },
  "request_id": "req-12345678",
  "timestamp": "2026-05-28T15:30:00Z"
}
```

### Common Error Codes

| Code | Status | Meaning |
|------|--------|---------|
| `UNAUTHORIZED` | 401 | Missing or invalid authentication token |
| `FORBIDDEN` | 403 | User lacks permission for this resource (e.g. billing payouts for another creator, streaming session owner checks) |
| `NOT_FOUND` | 404 | Resource does not exist |
| `VALIDATION_ERROR` | 422 | Request validation failed |
| `DUPLICATE_RESOURCE` | 409 | Resource already exists (unique constraint) — e.g. duplicate genre |
| `RATE_LIMITED` | 429 | Too many requests, wait before retrying (enforced by the api-gateway) |
| `INTERNAL_ERROR` | 500 | Unexpected server error |
| `SERVICE_UNAVAILABLE` | 503 | Service temporarily unavailable |

---

## Rate Limiting

The api-gateway enforces rate limits per client (authenticated user `sub` or IP):

| Endpoint | Limit | Window |
|----------|-------|--------|
| `/api/v1/auth/login` | 5 attempts | 5 minutes |
| `/api/v1/auth/register` | 3 registrations | 1 hour |
| **⚠️ `/search` — no such route.** The search limit below belonged to a path that does not exist | 100 requests | 1 minute |
| Most other endpoints | 1000 requests | 1 minute |

(Configured per service in `app/core/settings.py` — auth-service and
api-gateway ship concrete limiters; other services use gateway defaults.)

When rate limited (429):

```json
{"detail": "Rate limit exceeded"}
```

with a `Retry-After` header.

---

## Pagination

> ❌ **This section is wrong.** The envelope below does not exist. `has_next`
> and `has_prev` appear in no Pydantic schema in any of the 15 services —
> verified by grepping `services/**/app/schemas` and `apps/web/src/types` on
> 2026-10-14. The example path `/movies` is also not a real route.
>
> No service currently exposes a list endpoint with a documented pagination
> contract. Treat list handling as per-endpoint and read the endpoint's own
> `response_model` in `openapi.json`.

The shape that was documented here, for the record:

```http
GET /movies?limit=20&offset=40
```

Response includes:

```json
{
  "items": [...],
  "total": 1250,
  "limit": 20,
  "offset": 40,
  "has_next": true,
  "has_prev": true
}
```

Calculate pages:
- **Next offset**: `offset + limit`
- **Previous offset**: `max(0, offset - limit)`
- **Total pages**: `ceil(total / limit)`

---

## Webhooks (Future)

Wildframe will support webhooks for:
- User subscription changes
- Content added/updated
- Account events
- Billing notifications

(Currently in planning phase)

---

## See Also

- [Streaming Guide](DEVELOPMENT.md)
- [Testing API](../TEST_GUIDE.md#testing-apis)
- [Status Page](https://status.wildframe.com)
