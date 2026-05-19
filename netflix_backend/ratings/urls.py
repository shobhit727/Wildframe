from django.urls import path, include
from rest_framework.routers import DefaultRouter
from ratings.views import RatingViewSet, ReviewViewSet

router = DefaultRouter()
router.register(r'', RatingViewSet, basename='rating')
router.register(r'reviews', ReviewViewSet, basename='review')

urlpatterns = [
    path('', include(router.urls)),
]
