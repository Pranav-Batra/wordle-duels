from django.db import models
from django.contrib.auth.models import User
from django_resized import ResizedImageField

# Create your models here.
class Profile(models.Model):
    user = models.OneToOneField(User, on_delete = models.CASCADE)
    elo_rating = models.IntegerField(default = 1000)
    profile_pic = ResizedImageField(size=[150, 150], crop=['middle', 'center'], blank = True, null = True, upload_to='profile_pics/', default = 'default.jpg')

    def __str__(self):
        return f"{self.user.username}'s profile"