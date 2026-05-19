from django.db import models
from users.models import User
from content.models import Movie, Episode


class ViewingHistory(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='viewing_history')
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, null=True, blank=True)
    episode = models.ForeignKey(Episode, on_delete=models.CASCADE, null=True, blank=True)
    progress = models.IntegerField(default=0, help_text="Progress in percentage (0-100)")
    watch_time = models.IntegerField(default=0, help_text="Watch time in seconds")
    timestamp = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        content = self.movie.title if self.movie else f"{self.episode.show.title} - {self.episode}"
        return f"{self.user.email} - {content}"
