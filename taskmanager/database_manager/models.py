from django.db import models
from document.models import VariableBlock



class VariableSQLSet(models.Model):
    name = models.CharField('Название', max_length=100, null=True, blank=True)
    sql = models.CharField('SQL-название столбца', max_length=100, null=True, blank=True)

    def __str__(self):
        return f"{self.name}"

    class Meta:
        verbose_name = 'Поисковая sql-переменная'
        verbose_name_plural = 'Поисковые sql-переменные'

class VariableSQLGet(models.Model):
    name = models.CharField('Название', max_length=100, null=True, blank=True)
    sql = models.CharField('SQL-запрос', max_length=5000, null=True, blank=True)

    def __str__(self):
        return f"{self.name}"

    class Meta:
        verbose_name = 'Получаемая sql-переменная'
        verbose_name_plural = 'Получаемые sql-переменные'

class VariableSQLSet_VariableSQLGet(models.Model):
    variableSet = models.ForeignKey(VariableSQLSet, on_delete=models.CASCADE, null=True, blank=True)
    variableGet = models.ForeignKey(VariableSQLGet, on_delete=models.CASCADE, null=True, blank=True)

class Connection(models.Model):
    dialect = models.CharField('Диалект SQL', max_length=100, null=True, blank=True)
    username = models.CharField('Имя пользователя', max_length=100, null=True, blank=True)
    password = models.CharField('Пароль пользователя', max_length=100, null=True, blank=True)
    host = models.CharField('Адрес базы данных', max_length=100, null=True, blank=True)
    port = models.CharField('Порт для подключения', max_length=100, null=True, blank=True)
    service = models.CharField('Название сервиса', max_length=100, null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.dialect}//{self.host}@{self.username}"
    
    class Meta:
        verbose_name = 'Подключение'
        verbose_name_plural = 'Подключения'