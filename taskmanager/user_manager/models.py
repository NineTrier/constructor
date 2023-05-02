from django.db import models
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User


class Organisation(models.Model):
    class Meta:
        ordering = ['name']
    name = models.TextField(null=True, blank=True)

    def __str__(self):
        return self.name


class Profile(models.Model):

    class Meta:
        ordering = ['firstName', 'lastName', 'middleName']
    user = models.OneToOneField(User, null=True, on_delete=models.CASCADE)
    organisation = models.ForeignKey(Organisation, null=True, on_delete=models.CASCADE)
    firstName = models.TextField(null=True, blank=True)
    middleName = models.TextField(null=True, blank=True)
    lastName = models.TextField(null=True, blank=True)
    profile_pic = models.ImageField(null=True, blank=True, upload_to="images/profile/")

    def __str__(self):
        return f"{self.firstName} {self.lastName} {self.middleName}"
