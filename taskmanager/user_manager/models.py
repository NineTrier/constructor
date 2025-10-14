from django.contrib.auth import get_user_model
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


def user_directory_path(instance, filename):
    return f"user_{instance.user.id}/{filename}"


User = get_user_model()


class Organisation(models.Model):
    class Meta:
        ordering = ["name"]

    name = models.TextField(null=True, blank=True)

    def __str__(self) -> str:
        return self.name or "-"


class Profile(models.Model):
    class Meta:
        ordering = ["firstName", "lastName", "middleName"]

    user = models.OneToOneField(User, null=True, on_delete=models.CASCADE)
    organisation = models.ForeignKey(
        Organisation,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    canAddOrganisationDocument = models.BooleanField(default=False, null=True, blank=True)
    firstName = models.TextField(null=True, blank=True)
    middleName = models.TextField(null=True, blank=True)
    lastName = models.TextField(null=True, blank=True)
    profile_pic = models.ImageField(null=True, blank=True, upload_to=user_directory_path)

    def __str__(self) -> str:
        parts = [self.lastName, self.firstName, self.middleName]
        name = " ".join(part for part in parts if part).strip()
        if name:
            return name
        return self.user.get_username() if self.user else ""

    @classmethod
    def for_user(cls, user):
        if user is None:
            return None
        defaults = {
            "firstName": getattr(user, "first_name", "") or "",
            "lastName": getattr(user, "last_name", "") or "",
            "middleName": "",
        }
        organisation = Organisation.objects.order_by("id").first()
        if organisation:
            defaults["organisation"] = organisation
        profile, created = cls.objects.get_or_create(user=user, defaults=defaults)
        if created and not profile.organisation and organisation:
            profile.organisation = organisation
            profile.save(update_fields=["organisation"])
        return profile

    def getAbbr(self) -> str:
        return self.abbr

    @property
    def abbr(self) -> str:
        base = self.lastName or (self.user.get_username() if self.user else "")
        initials = []
        if self.firstName:
            initials.append(f"{self.firstName[0]}.")
        if self.middleName:
            initials.append(f"{self.middleName[0]}.")
        suffix = " ".join(initials)
        return f"{base} {suffix}".strip()


@receiver(post_save, sender=User)
def ensure_profile_exists(sender, instance, created, **kwargs):
    if created:
        Profile.for_user(instance)
