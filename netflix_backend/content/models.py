from django.db import models


class Genre(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Movie(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField()
    release_date = models.DateField()
    runtime = models.IntegerField(help_text="Runtime in minutes")
    genre = models.ForeignKey(Genre, on_delete=models.SET_NULL, null=True, related_name='movies')
    poster_url = models.URLField(blank=True, null=True)
    rating_avg = models.FloatField(default=0, help_text="Average rating from 0-10")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-release_date']

    def __str__(self):
        return self.title


class Show(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField()
    release_date = models.DateField()
    genre = models.ForeignKey(Genre, on_delete=models.SET_NULL, null=True, related_name='shows')
    poster_url = models.URLField(blank=True, null=True)
    rating_avg = models.FloatField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-release_date']

    def __str__(self):
        return self.title


class Episode(models.Model):
    show = models.ForeignKey(Show, on_delete=models.CASCADE, related_name='episodes')
    season_number = models.IntegerField()
    episode_number = models.IntegerField()
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    duration = models.IntegerField(help_text="Duration in seconds")
    aired_date = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['season_number', 'episode_number']
        unique_together = ('show', 'season_number', 'episode_number')

    def __str__(self):
        return f"{self.show.title} - S{self.season_number}E{self.episode_number}: {self.title}"


class VideoFile(models.Model):
    RESOLUTION_CHOICES = [
        ('480p', '480p'),
        ('720p', '720p'),
        ('1080p', '1080p'),
        ('4k', '4K'),
    ]
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE, related_name='video_files', null=True, blank=True)
    episode = models.ForeignKey(Episode, on_delete=models.CASCADE, related_name='video_files', null=True, blank=True)
    resolution = models.CharField(max_length=10, choices=RESOLUTION_CHOICES)
    bitrate = models.IntegerField(help_text="Bitrate in kbps")
    file_url = models.URLField()
    file_size = models.BigIntegerField(help_text="File size in bytes")
    duration = models.IntegerField(help_text="Duration in seconds")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('movie', 'episode', 'resolution')

    def __str__(self):
        content = self.movie.title if self.movie else f"{self.episode.show.title} - {self.episode}"
        return f"{content} - {self.resolution}"
