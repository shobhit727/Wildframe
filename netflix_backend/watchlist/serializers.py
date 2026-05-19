from rest_framework import serializers
from watchlist.models import Watchlist, WatchlistMovie, WatchlistShow
from content.serializers import MovieListSerializer, ShowListSerializer


class WatchlistMovieSerializer(serializers.ModelSerializer):
    movie = MovieListSerializer(read_only=True)

    class Meta:
        model = WatchlistMovie
        fields = ['id', 'movie', 'is_favorite', 'added_date']


class WatchlistShowSerializer(serializers.ModelSerializer):
    show = ShowListSerializer(read_only=True)

    class Meta:
        model = WatchlistShow
        fields = ['id', 'show', 'is_favorite', 'added_date']


class WatchlistSerializer(serializers.ModelSerializer):
    movies = WatchlistMovieSerializer(many=True, read_only=True, source='watchlistmovie_set')
    shows = WatchlistShowSerializer(many=True, read_only=True, source='watchlistshow_set')

    class Meta:
        model = Watchlist
        fields = ['id', 'user', 'movies', 'shows', 'created_at', 'updated_at']
        read_only_fields = ['user', 'created_at', 'updated_at']
