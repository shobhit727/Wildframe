from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from watchlist.models import Watchlist, WatchlistMovie, WatchlistShow
from watchlist.serializers import WatchlistSerializer, WatchlistMovieSerializer, WatchlistShowSerializer


class WatchlistViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['get'])
    def my_watchlist(self, request):
        watchlist, _ = Watchlist.objects.get_or_create(user=request.user)
        serializer = WatchlistSerializer(watchlist)
        return Response(serializer.data)


class WatchlistMovieViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'])
    def add(self, request):
        watchlist, _ = Watchlist.objects.get_or_create(user=request.user)
        movie_id = request.data.get('movie_id')
        if not movie_id:
            return Response({'error': 'movie_id required'}, status=status.HTTP_400_BAD_REQUEST)

        item, created = WatchlistMovie.objects.get_or_create(
            watchlist=watchlist,
            movie_id=movie_id
        )
        serializer = WatchlistMovieSerializer(item)
        return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(detail=False, methods=['post'])
    def remove(self, request):
        watchlist, _ = Watchlist.objects.get_or_create(user=request.user)
        movie_id = request.data.get('movie_id')
        if not movie_id:
            return Response({'error': 'movie_id required'}, status=status.HTTP_400_BAD_REQUEST)

        WatchlistMovie.objects.filter(watchlist=watchlist, movie_id=movie_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WatchlistShowViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['post'])
    def add(self, request):
        watchlist, _ = Watchlist.objects.get_or_create(user=request.user)
        show_id = request.data.get('show_id')
        if not show_id:
            return Response({'error': 'show_id required'}, status=status.HTTP_400_BAD_REQUEST)

        item, created = WatchlistShow.objects.get_or_create(
            watchlist=watchlist,
            show_id=show_id
        )
        serializer = WatchlistShowSerializer(item)
        return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(detail=False, methods=['post'])
    def remove(self, request):
        watchlist, _ = Watchlist.objects.get_or_create(user=request.user)
        show_id = request.data.get('show_id')
        if not show_id:
            return Response({'error': 'show_id required'}, status=status.HTTP_400_BAD_REQUEST)

        WatchlistShow.objects.filter(watchlist=watchlist, show_id=show_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
