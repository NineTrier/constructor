from django.db import models
from django.contrib.auth.models import User
from user_manager.models import Profile


def user_directory_path(instance, filename):
    return f'user_{instance.user.id}/{filename}'


class DocType(models.Model):
    id = models.BigIntegerField(primary_key=True)
    name = models.CharField('Название', max_length=100, null=True, blank=True)
    author = models.ForeignKey(Profile, on_delete=models.CASCADE, null=True, blank=True)

    def __str__(self):
        return f"{self.name}"

    class Meta:
        verbose_name = 'Тип документа'
        verbose_name_plural = 'Типы документов'


# Класс документы для базы данных
class Documents(models.Model):
    name = models.CharField('Название', max_length=100, null=True, blank=True)
    owner = models.CharField('Владелец', max_length=100, null=True, blank=True)
    type = models.ForeignKey(DocType, on_delete=models.CASCADE, default=1, null=True, blank=True)
    description = models.TextField('Описание', null=True, blank=True)
    file = models.FileField(upload_to='documents/', null=True, blank=True)
    json = models.JSONField('JSON строка', default=dict, null=True, blank=True)
    author = models.ForeignKey(Profile, on_delete=models.CASCADE, null=True, blank=True)

    def __str__(self):
        return f"{self.name} от {self.owner}"

    def save(self, *args, **kwargs):
        try:
            super().save(*args, **kwargs)  # Call the "real" save() method.
            return True
        except:
            return False

    class Meta:
        verbose_name = 'Документ'
        verbose_name_plural = 'Документы'


class VariableBlock(models.Model):
    name = models.CharField('Название', max_length=100, null=True, blank=True)
    doc = models.ForeignKey(Documents, on_delete=models.CASCADE, null=True, blank=True)
    meaning = models.TextField('Значение', null=True, blank=True)
    author = models.ForeignKey(Profile, on_delete=models.CASCADE, null=True, blank=True)

    def __str__(self):
        return f"{self.name}"

    class Meta:
        verbose_name = 'Переменная'
        verbose_name_plural = 'Переменные'


class Fonts(models.Model):
    name = models.CharField('Название', max_length=100, null=True, blank=True)

    def __str__(self):
        return f"{self.name}"

    class Meta:
        verbose_name = 'Шрифт'
        verbose_name_plural = 'Шрифты'


class SavedElements(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.CharField('Название', max_length=100, null=True, blank=True)
    type = models.ForeignKey(DocType, on_delete=models.CASCADE, default=1, null=True, blank=True)
    json = models.JSONField('JSON строка', null=True, blank=True)
    author = models.ForeignKey(Profile, on_delete=models.CASCADE, null=True, blank=True)

    def __str__(self):
        return f"{self.name}"

    class Meta:
        verbose_name = 'Сохраненный элемент'
        verbose_name_plural = 'Сохраненные элементы'
