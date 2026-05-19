# Agent Instructions for Wildframe Netflix Backend

This Django REST Framework project implements a Netflix-like streaming platform backend.

## Essential Setup
- Activate virtual environment: `source venv/bin/activate`
- Install dependencies: `pip install -r requirements.txt`
- Run migrations: `python manage.py migrate`
- Start development server: `python manage.py runserver`

## Project Structure
- **Apps**: users, content, watchlist, history, ratings, streaming, subscriptions
- **API prefix**: `/api/` for all endpoints
- **Authentication**: JWT tokens (access: 15min, refresh: 7 days)

## Code Conventions
- Models include `created_at`, `updated_at` timestamps and `is_active` for soft deletes
- Use ViewSets with custom actions for complex endpoints
- Separate serializers for list vs detail views
- Signals auto-create related models (Profile, Watchlist) on User creation
- Foreign keys use `related_name` for reverse lookups

## Common Patterns
- Nested URL patterns for related resources (e.g., `/api/watchlist/movies/`)
- Filtering and search enabled on content endpoints
- Pagination on list views
- Permissions: authenticated by default, AllowAny for public content

## Key Files
- Settings: `netflix_backend/settings.py` (environment-based config)
- Models: `content/models.py` (Genre → Movie/Show → Episode hierarchy)
- Views: `content/views.py` (ViewSet patterns)
- URLs: `netflix_backend/urls.py` (router registration)

## Pitfalls to Avoid
- Always activate venv before Python commands
- Configure `.env` file with required variables
- Run migrations after model changes
- SQLite default DB not suitable for production concurrency

## Documentation
- [Setup and API overview](netflix_backend/README.md)
- [High-level architecture vision](ARCHITECTURE.md) (aspirational microservices design)</content>
<parameter name="filePath">/home/phoenix/Desktop/wildframe/AGENTS.md