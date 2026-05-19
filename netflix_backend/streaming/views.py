from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from streaming.models import StreamingLog
from streaming.serializers import StreamingLogSerializer

class StreamingLogViewSet(viewsets.ModelViewSet):
    serializer_class = StreamingLogSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return StreamingLog.objects.filter(user=self.request.user)
