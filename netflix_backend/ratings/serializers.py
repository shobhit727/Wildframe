from rest_framework import serializers
from ratings.models import Rating, Review, ReviewLike
from users.serializers import UserSerializer


class RatingSerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(source='user.email', read_only=True)

    class Meta:
        model = Rating
        fields = ['id', 'user_email', 'movie', 'show', 'score', 'created_at', 'updated_at']
        read_only_fields = ['user', 'created_at', 'updated_at']


class ReviewLikeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewLike
        fields = ['id', 'user', 'review']


class ReviewSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    liked_by_count = serializers.SerializerMethodField()

    class Meta:
        model = Review
        fields = ['id', 'user', 'movie', 'show', 'title', 'text', 'likes', 'liked_by_count', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at', 'likes']

    def get_liked_by_count(self, obj):
        return obj.liked_by.count()
