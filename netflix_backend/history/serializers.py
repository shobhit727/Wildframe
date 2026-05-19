from rest_framework import serializers
from history.models import ViewingHistory


class ViewingHistorySerializer(serializers.ModelSerializer):
    content_title = serializers.SerializerMethodField()
    content_type = serializers.SerializerMethodField()

    class Meta:
        model = ViewingHistory
        fields = ['id', 'movie', 'episode', 'content_title', 'content_type', 'progress', 'watch_time', 'timestamp', 'is_active']
        read_only_fields = ['timestamp']

    def get_content_title(self, obj):
        if obj.movie:
            return obj.movie.title
        elif obj.episode:
            return f"{obj.episode.show.title} - S{obj.episode.season_number}E{obj.episode.episode_number}"
        return None

    def get_content_type(self, obj):
        if obj.movie:
            return 'movie'
        elif obj.episode:
            return 'episode'
        return None
