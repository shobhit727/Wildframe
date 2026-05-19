from rest_framework import serializers
from streaming.models import StreamingLog


class StreamingLogSerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(source='user.email', read_only=True)
    content_title = serializers.SerializerMethodField()

    class Meta:
        model = StreamingLog
        fields = ['id', 'user', 'user_email', 'movie', 'episode', 'content_title', 'resolution', 'bytes_streamed', 'duration_watched', 'timestamp']
        read_only_fields = ['user', 'timestamp']

    def get_content_title(self, obj):
        if obj.movie:
            return obj.movie.title
        elif obj.episode:
            return f"{obj.episode.show.title} - S{obj.episode.season_number}E{obj.episode.episode_number}"
        return None
