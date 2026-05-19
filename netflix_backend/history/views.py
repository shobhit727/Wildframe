from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from history.models import ViewingHistory
from history.serializers import ViewingHistorySerializer

class ViewingHistoryViewSet(viewsets.ModelViewSet):
    serializer_class = ViewingHistorySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ViewingHistory.objects.filter(user=self.request.user)
