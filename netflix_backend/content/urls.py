from django.urls import path, include
from rest_framework.routers import DefaultRouter
from content.views import GenreViewSet, MovieViewSet, ShowViewSet, EpisodeViewSet

router = DefaultRouter()
router.register(r'genres', GenreViewSet, basename='genre')
router.register(r'movies', MovieViewSet, basename='movie')
router.register(r'shows', ShowViewSet, basename='show')
router.register(r'episodes', EpisodeViewSet, basename='episode')

urlpatterns = [
    path('', include(router.urls)),
]
