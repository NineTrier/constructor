from django.db import models
from django.contrib.auth.models import User


def user_directory_path(instance, filename):
    return f'user_{instance.user.id}/{filename}'


class DocType(models.Model):
    id = models.BigIntegerField(primary_key=True)
    name = models.CharField('Название', max_length=100)

    def __str__(self):
        return f"{self.name}"

    class Meta:
        verbose_name = 'Тип документа'
        verbose_name_plural = 'Типы документов'


# Класс документы для базы данных
class Documents(models.Model):
    name = models.CharField('Название', max_length=100)
    owner = models.CharField('Владелец', max_length=100)
    type = models.ForeignKey(DocType, on_delete=models.CASCADE, default=1)
    description = models.TextField('Описание')
    file = models.FileField(upload_to='documents/')
    json = models.JSONField('JSON строка', default=dict)

    def __str__(self):
        return f"{self.name} от {self.owner}"

    class Meta:
        verbose_name = 'Документ'
        verbose_name_plural = 'Документы'


class VariableBlock(models.Model):
    name = models.CharField('Название', max_length=100)
    doc = models.ForeignKey(Documents, on_delete=models.CASCADE)
    meaning = models.TextField('Значение')

    def __str__(self):
        return f"{self.name}"

    class Meta:
        verbose_name = 'Переменная'
        verbose_name_plural = 'Переменные'


class Fonts(models.Model):
    name = models.CharField('Название', max_length=100)

    def __str__(self):
        return f"{self.name}"

    class Meta:
        verbose_name = 'Шрифт'
        verbose_name_plural = 'Шрифты'


class SavedElements(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.CharField('Название', max_length=100)
    type = models.ForeignKey(DocType, on_delete=models.CASCADE, default=1)
    json = models.JSONField('JSON строка')

    def __str__(self):
        return f"{self.name}"

    class Meta:
        verbose_name = 'Сохраненный элемент'
        verbose_name_plural = 'Сохраненные элементы'
