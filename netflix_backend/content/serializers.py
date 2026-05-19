from rest_framework import serializers
from content.models import Genre, Movie, Show, Episode, VideoFile


class GenreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Genre
        fields = ['id', 'name', 'description']


class VideoFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoFile
        fields = ['id', 'resolution', 'bitrate', 'file_url', 'file_size', 'duration']


class EpisodeSerializer(serializers.ModelSerializer):
    video_files = VideoFileSerializer(many=True, read_only=True)

    class Meta:
        model = Episode
        fields = ['id', 'season_number', 'episode_number', 'title', 'description', 'duration', 'aired_date', 'video_files']


class MovieSerializer(serializers.ModelSerializer):
    genre_name = serializers.CharField(source='genre.name', read_only=True)
    video_files = VideoFileSerializer(many=True, read_only=True)

    class Meta:
        model = Movie
        fields = ['id', 'title', 'description', 'release_date', 'runtime', 'genre', 'genre_name', 'poster_url', 'rating_avg', 'is_active', 'created_at', 'video_files']
        read_only_fields = ['created_at', 'rating_avg']


class ShowSerializer(serializers.ModelSerializer):
    genre_name = serializers.CharField(source='genre.name', read_only=True)
    episodes = EpisodeSerializer(many=True, read_only=True)

    class Meta:
        model = Show
        fields = ['id', 'title', 'description', 'release_date', 'genre', 'genre_name', 'poster_url', 'rating_avg', 'is_active', 'created_at', 'episodes']
        read_only_fields = ['created_at', 'rating_avg']


class MovieListSerializer(serializers.ModelSerializer):
    genre_name = serializers.CharField(source='genre.name', read_only=True)

    class Meta:
        model = Movie
        fields = ['id', 'title', 'release_date', 'genre_name', 'poster_url', 'rating_avg']


class ShowListSerializer(serializers.ModelSerializer):
    genre_name = serializers.CharField(source='genre.name', read_only=True)

    class Meta:
        model = Show
        fields = ['id', 'title', 'release_date', 'genre_name', 'poster_url', 'rating_avg']
