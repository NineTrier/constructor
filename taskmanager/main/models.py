from django.db import models
from django.contrib.auth.models import User


def user_directory_path(instance, filename):
    return f'user_{instance.user.id}/{filename}'


# Класс документы для базы данных
class Documents(models.Model):
    name = models.CharField('Название', max_length=100)
    owner = models.CharField('Владелец', max_length=100)
    description = models.TextField('Описание')
    file = models.FileField(upload_to='documents/')

    def __str__(self):
        return f"{self.name} от {self.owner}"

    class Meta:
        verbose_name = 'Документ'
        verbose_name_plural = 'Документы'


class Fonts(models.Model):
    name = models.CharField('Название', max_length=100)

    def __str__(self):
        return f"{self.name}"

    class Meta:
        verbose_name = 'Шрифт'
        verbose_name_plural = 'Шрифты'
