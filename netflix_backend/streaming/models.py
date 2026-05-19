from django.db import models
from users.models import User
from content.models import Movie, Episode


class StreamingLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='streaming_logs')
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, null=True, blank=True)
    episode = models.ForeignKey(Episode, on_delete=models.CASCADE, null=True, blank=True)
    resolution = models.CharField(
        max_length=10,
        choices=[('480p', '480p'), ('720p', '720p'), ('1080p', '1080p'), ('4k', '4K')]
    )
    bytes_streamed = models.BigIntegerField(default=0)
    duration_watched = models.IntegerField(default=0, help_text="Duration watched in seconds")
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        content = self.movie.title if self.movie else f"{self.episode.show.title}"
        return f"{self.user.email} - {content} - {self.resolution}"
