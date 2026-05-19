from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from users.models import User
from content.models import Movie, Show


class Rating(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ratings')
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, null=True, blank=True, related_name='ratings')
    show = models.ForeignKey(Show, on_delete=models.CASCADE, null=True, blank=True, related_name='ratings')
    score = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(10)]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'movie', 'show')
        ordering = ['-created_at']

    def __str__(self):
        content = self.movie.title if self.movie else self.show.title
        return f"{self.user.email} - {content} - {self.score}/10"


class Review(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews')
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, null=True, blank=True, related_name='reviews')
    show = models.ForeignKey(Show, on_delete=models.CASCADE, null=True, blank=True, related_name='reviews')
    title = models.CharField(max_length=200)
    text = models.TextField()
    likes = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        content = self.movie.title if self.movie else self.show.title
        return f"{self.user.email} - {content}: {self.title}"


class ReviewLike(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='review_likes')
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name='liked_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'review')

    def __str__(self):
        return f"{self.user.email} liked {self.review.title}"
