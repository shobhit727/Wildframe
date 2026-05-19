from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from users.views import RegisterView, UserViewSet
from content.views import GenreViewSet, MovieViewSet, ShowViewSet, EpisodeViewSet
from watchlist.views import WatchlistViewSet, WatchlistMovieViewSet, WatchlistShowViewSet
from ratings.views import RatingViewSet, ReviewViewSet
from history.views import ViewingHistoryViewSet
from streaming.views import StreamingLogViewSet
from subscriptions.views import SubscriptionPlanViewSet, SubscriptionViewSet, PaymentViewSet
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

router = DefaultRouter()

# Auth
router.register(r'auth/register', RegisterView, basename='register')
router.register(r'users', UserViewSet, basename='user')

# Content
router.register(r'genres', GenreViewSet, basename='genre')
router.register(r'movies', MovieViewSet, basename='movie')
router.register(r'shows', ShowViewSet, basename='show')
router.register(r'episodes', EpisodeViewSet, basename='episode')

# User Interactions
router.register(r'watchlist/movies', WatchlistMovieViewSet, basename='watchlist-movie')
router.register(r'watchlist/shows', WatchlistShowViewSet, basename='watchlist-show')
router.register(r'watchlist', WatchlistViewSet, basename='watchlist')
router.register(r'ratings', RatingViewSet, basename='rating')
router.register(r'reviews', ReviewViewSet, basename='review')
router.register(r'history', ViewingHistoryViewSet, basename='history')

# Streaming & Subscriptions
router.register(r'streaming', StreamingLogViewSet, basename='streaming')
router.register(r'subscriptions/plans', SubscriptionPlanViewSet, basename='plan')
router.register(r'subscriptions', SubscriptionViewSet, basename='subscription')
router.register(r'payments', PaymentViewSet, basename='payment')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include(router.urls)),
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
]
