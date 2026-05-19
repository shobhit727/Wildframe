from django.urls import path, include
from rest_framework.routers import DefaultRouter
from history.views import ViewingHistoryViewSet

router = DefaultRouter()
router.register(r'', ViewingHistoryViewSet, basename='history')

urlpatterns = [
    path('', include(router.urls)),
]
