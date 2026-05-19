from django.db import models
from users.models import User
from content.models import Movie, Show


class Watchlist(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='watchlist')
    movies = models.ManyToManyField(Movie, through='WatchlistMovie', related_name='watchlist_users')
    shows = models.ManyToManyField(Show, through='WatchlistShow', related_name='watchlist_users')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.email}'s watchlist"


class WatchlistMovie(models.Model):
    watchlist = models.ForeignKey(Watchlist, on_delete=models.CASCADE)
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE)
    is_favorite = models.BooleanField(default=False)
    added_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('watchlist', 'movie')

    def __str__(self):
        return f"{self.watchlist.user.email} - {self.movie.title}"


class WatchlistShow(models.Model):
    watchlist = models.ForeignKey(Watchlist, on_delete=models.CASCADE)
    show = models.ForeignKey(Show, on_delete=models.CASCADE)
    is_favorite = models.BooleanField(default=False)
    added_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('watchlist', 'show')

    def __str__(self):
        return f"{self.watchlist.user.email} - {self.show.title}"
