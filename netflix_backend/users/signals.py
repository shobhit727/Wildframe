from django.db.models.signals import post_save
from django.dispatch import receiver
from users.models import User, Profile
from watchlist.models import Watchlist


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)


@receiver(post_save, sender=User)
def create_user_watchlist(sender, instance, created, **kwargs):
    if created:
        Watchlist.objects.create(user=instance)
