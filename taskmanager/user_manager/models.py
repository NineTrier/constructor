from django.db import models
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User


def user_directory_path(instance, filename):
    # путь, куда будет осуществлена загрузка MEDIA_ROOT/user_<id>/<filename>
    return 'user_{0}/{1}'.format(instance.user.id, filename)

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
    canAddOrganisationDocument = models.BooleanField(default=False ,null=True, blank=True)
    firstName = models.TextField(null=True, blank=True)
    middleName = models.TextField(null=True, blank=True)
    lastName = models.TextField(null=True, blank=True)
    profile_pic = models.ImageField(null=True, blank=True, upload_to=user_directory_path)

    def __str__(self):
        return f"{self.lastName} {self.firstName} {self.middleName}"
    
    def getAbbr(self):
        print(self.lastName)
        print(self.firstName)
        print(self.middleName)
        return f"{self.lastName} {self.firstName[0]}. {self.middleName[0]}."
