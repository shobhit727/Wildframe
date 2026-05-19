from django.urls import path, include
from rest_framework.routers import DefaultRouter
from watchlist.views import WatchlistViewSet, WatchlistMovieViewSet, WatchlistShowViewSet

router = DefaultRouter()
router.register(r'movies', WatchlistMovieViewSet, basename='watchlist-movie')
router.register(r'shows', WatchlistShowViewSet, basename='watchlist-show')
router.register(r'', WatchlistViewSet, basename='watchlist')

urlpatterns = [
    path('', include(router.urls)),
]
