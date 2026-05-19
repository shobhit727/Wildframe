from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Avg, Count

from content.models import Genre, Movie, Show, Episode
from content.serializers import GenreSerializer, MovieSerializer, ShowSerializer, MovieListSerializer, ShowListSerializer, EpisodeSerializer
from history.models import ViewingHistory


class GenreViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Genre.objects.all()
    serializer_class = GenreSerializer
    permission_classes = [AllowAny]


class MovieViewSet(viewsets.ModelViewSet):
    queryset = Movie.objects.filter(is_active=True)
    serializer_class = MovieSerializer
    permission_classes = [AllowAny]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['genre', 'release_date']
    search_fields = ['title', 'description']
    ordering_fields = ['release_date', 'rating_avg', 'created_at']
    ordering = ['-release_date']

    def get_serializer_class(self):
        if self.action == 'list':
            return MovieListSerializer
        return MovieSerializer

    @action(detail=False, methods=['get'])
    def trending(self, request):
        trending_movies = ViewingHistory.objects.filter(
            movie__isnull=False
        ).values('movie').annotate(
            views=Count('id')
        ).order_by('-views')[:10]

        movie_ids = [item['movie'] for item in trending_movies]
        movies = Movie.objects.filter(id__in=movie_ids)
        serializer = MovieListSerializer(movies, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def reviews(self, request, pk=None):
        movie = self.get_object()
        reviews = movie.reviews.all()
        from ratings.serializers import ReviewSerializer
        serializer = ReviewSerializer(reviews, many=True)
        return Response(serializer.data)


class ShowViewSet(viewsets.ModelViewSet):
    queryset = Show.objects.filter(is_active=True)
    serializer_class = ShowSerializer
    permission_classes = [AllowAny]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['genre', 'release_date']
    search_fields = ['title', 'description']
    ordering_fields = ['release_date', 'rating_avg', 'created_at']
    ordering = ['-release_date']

    def get_serializer_class(self):
        if self.action == 'list':
            return ShowListSerializer
        return ShowSerializer

    @action(detail=False, methods=['get'])
    def trending(self, request):
        trending_shows = ViewingHistory.objects.filter(
            episode__show__isnull=False
        ).values('episode__show').annotate(
            views=Count('id')
        ).order_by('-views')[:10]

        show_ids = [item['episode__show'] for item in trending_shows]
        shows = Show.objects.filter(id__in=show_ids)
        serializer = ShowListSerializer(shows, many=True)
        return Response(serializer.data)


class EpisodeViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = EpisodeSerializer
    permission_classes = [AllowAny]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['show', 'season_number']

    def get_queryset(self):
        show_id = self.request.query_params.get('show')
        if show_id:
            return Episode.objects.filter(show_id=show_id)
        return Episode.objects.all()
