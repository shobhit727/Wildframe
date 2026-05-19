from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated, AllowAny
from ratings.models import Rating, Review
from ratings.serializers import RatingSerializer, ReviewSerializer


class RatingViewSet(viewsets.ModelViewSet):
    queryset = Rating.objects.all()
    serializer_class = RatingSerializer
    permission_classes = [IsAuthenticated]


class ReviewViewSet(viewsets.ModelViewSet):
    queryset = Review.objects.all()
    serializer_class = ReviewSerializer
    permission_classes = [AllowAny]
