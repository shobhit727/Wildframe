from django.urls import path, include
from rest_framework.routers import DefaultRouter
from streaming.views import StreamingLogViewSet

router = DefaultRouter()
router.register(r'', StreamingLogViewSet, basename='streaming')

urlpatterns = [
    path('', include(router.urls)),
]
