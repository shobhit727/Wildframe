# Netflix Backend API

Complete Django REST Framework backend for a Netflix-like streaming platform.

## Setup

1. **Install dependencies**:
```bash
source venv/bin/activate
pip install -r requirements.txt
```

2. **Configure environment** (create `.env` file):
```
SECRET_KEY=your-secret-key
DEBUG=True
DB_ENGINE=django.db.backends.sqlite3
DB_NAME=db.sqlite3
ALLOWED_HOSTS=localhost,127.0.0.1
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

3. **Run migrations**:
```bash
python manage.py migrate
```

4. **Create superuser**:
```bash
python manage.py createsuperuser
```

5. **Start server**:
```bash
python manage.py runserver
```

## API Endpoints

### Authentication
- `POST /api/auth/register/` - Register new user
- `POST /api/token/` - Get JWT token
- `POST /api/token/refresh/` - Refresh access token
- `GET /api/users/me/` - Get current user
- `GET|PATCH /api/users/profile/` - Get/update user profile

### Content
- `GET /api/genres/` - List genres
- `GET /api/movies/` - List movies (with search & filtering)
- `GET /api/movies/{id}/` - Get movie details
- `GET /api/movies/trending/` - Get trending movies
- `GET /api/shows/` - List shows
- `GET /api/shows/{id}/` - Get show details
- `GET /api/episodes/` - List episodes

### Watchlist
- `POST /api/watchlist/movies/add/` - Add movie to watchlist
- `POST /api/watchlist/movies/remove/` - Remove movie
- `POST /api/watchlist/shows/add/` - Add show to watchlist
- `POST /api/watchlist/shows/remove/` - Remove show
- `GET /api/watchlist/my_watchlist/` - Get user's watchlist

### History & Ratings
- `GET /api/history/` - Get viewing history
- `POST /api/history/` - Add to viewing history
- `GET /api/ratings/` - Get user's ratings
- `POST /api/ratings/` - Rate content
- `GET /api/reviews/` - List reviews
- `POST /api/reviews/` - Create review

### Streaming & Subscriptions
- `GET /api/subscriptions/plans/` - List subscription plans
- `POST /api/subscriptions/` - Subscribe to plan
- `GET /api/streaming/` - Get streaming logs

## Key Features

✅ User authentication with JWT tokens  
✅ Browse movies, shows, and episodes  
✅ Search and filter content  
✅ Watchlist and favorites  
✅ Viewing history tracking  
✅ Rating and review system  
✅ Subscription management  
✅ Streaming analytics  
✅ Full-text search support  
✅ Pagination and filtering  

## Database Models

- **User** - Custom user model with subscription tier
- **Profile** - User profile with bio and preferences  
- **Genre** - Content categories
- **Movie** - Movie metadata and files
- **Show** - TV show metadata
- **Episode** - Individual episodes with video files
- **VideoFile** - Multi-resolution video files
- **Watchlist** - User's saved content
- **ViewingHistory** - User's watch progress
- **Rating** - Content ratings (1-10)
- **Review** - Detailed reviews with likes
- **Subscription** - User subscription details
- **Payment** - Payment transaction records

## Development

Run tests:
```bash
python manage.py test
```

Access admin:
```
http://localhost:8000/admin/
```

## Next Steps

1. Add more comprehensive tests
2. Implement actual video streaming with adaptive bitrate
3. Add payment integration (Stripe/PayPal)
4. Implement recommendation engine
5. Add real-time notifications
6. Deploy to production
