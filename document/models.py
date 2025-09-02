from django.db import models
from django.contrib.auth.models import User
from user_manager.models import Profile, Organisation
from database_manager.models import Object


def user_directory_path(instance, filename):
    return f'documents/user_{instance.user.id}/{filename}'


class DocType(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.CharField(verbose_name='Название', max_length=100, null=True, blank=True)
    author = models.ForeignKey(Profile, on_delete=models.CASCADE, null=True, blank=True)
    def __str__(self):
        return f"{self.name}"

    class Meta:
        verbose_name = 'Тип документа'
        verbose_name_plural = 'Типы документов'


# Класс документы для базы данных
class DocumentsPattern(models.Model):
    name = models.CharField(verbose_name='Название', max_length=100, null=True, blank=True)
    type = models.ForeignKey(DocType, on_delete=models.CASCADE, default=1, null=True, blank=True)
    description = models.TextField(verbose_name='Описание', null=True, blank=True)
    file = models.FileField(upload_to=user_directory_path, null=True, blank=True)
    json = models.JSONField(verbose_name='JSON строка', default=dict, null=True, blank=True)
    owner = models.ForeignKey(Profile, on_delete=models.CASCADE, null=True, blank=True)
    documentOfOrganisation = models.BooleanField(default=False, null=True, blank=True)
    picture = models.ImageField(null=True, blank=True, upload_to=user_directory_path)
    lastUpdate = models.DateTimeField(auto_now=True, null=True, blank=True)
    dateCreate = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    downloadsTimes = models.PositiveIntegerField(default=0,null=True, blank=True)
    

    def __str__(self):
        return f"{self.name} от {self.owner}"

    def save(self, *args, **kwargs):
        try:
            super().save(*args, **kwargs)  # Call the "real" save() method.
            return True
        except:
            return False

    def print_lastUpdate_in_other_format(self):
        return self.lastUpdate.strftime('%d.%m.%Y')

    class Meta:
        verbose_name = 'Документ'
        verbose_name_plural = 'Документы'



#TO DO Убрать как ненужное
class VariableBlock(models.Model):
    name = models.CharField(verbose_name='Название', max_length=100, null=True, blank=True)
    meaning = models.TextField(verbose_name='Значение', null=True, blank=True)
    
    def __str__(self):
        return f"{self.name}"

    class Meta:
        verbose_name = 'Переменная'
        verbose_name_plural = 'Переменные'
        
    
class Document_VariableBlock(models.Model):
    document = models.ForeignKey(DocumentsPattern, on_delete=models.CASCADE, null=True, blank=True)
    variable = models.ForeignKey(VariableBlock, on_delete=models.CASCADE, null=True, blank=True)


class Fonts(models.Model):
    name = models.CharField(verbose_name='Название', max_length=100, null=True, blank=True)

    def __str__(self):
        return f"{self.name}"

    class Meta:
        verbose_name = 'Шрифт'
        verbose_name_plural = 'Шрифты'


class SavedElements(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.CharField(verbose_name='Название', max_length=100, null=True, blank=True)
    type = models.ForeignKey(DocType, on_delete=models.CASCADE, default=1, null=True, blank=True)
    json = models.JSONField(verbose_name='JSON строка', null=True, blank=True)
    author = models.ForeignKey(Profile, on_delete=models.CASCADE, null=True, blank=True)

    def __str__(self):
        return f"{self.name}"

    class Meta:
        verbose_name = 'Сохраненный элемент'
        verbose_name_plural = 'Сохраненные элементы'


class Document_ParentDocument(models.Model):
    document = models.ForeignKey(DocumentsPattern, on_delete=models.CASCADE, null=True, blank=True, related_name='document')
    parent = models.ForeignKey(DocumentsPattern, on_delete=models.CASCADE, null=True, blank=True, related_name='parent_document')
    parentDocumentChanged = models.BooleanField(default=False, null=True, blank=True)
    userRequested = models.ForeignKey(Profile, on_delete=models.CASCADE, null=True, blank=True)


class GroupOfDocument(models.Model):
    name = models.CharField(verbose_name='Название группы', max_length=100, null=True, blank=True)


class CreatedDocument(models.Model):
    name = models.CharField(verbose_name='Название документа', max_length=100, null=True, blank=True)
    groupofdocument = models.ForeignKey(GroupOfDocument, on_delete=models.CASCADE, null=True, blank=True)
    file = models.FileField(upload_to=user_directory_path, null=True, blank=True)
    json = models.JSONField('JSON строка', default=dict, null=True, blank=True)
    pattern = models.ForeignKey(DocumentsPattern, on_delete=models.CASCADE, null=True, blank=True)
    lastUpdate = models.DateTimeField(auto_now=True, null=True, blank=True)
    dateCreate = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    downloadsTimes = models.PositiveIntegerField(default=0,null=True, blank=True)
    
class DocumentPattern_Objects(models.Model):
    document = models.ForeignKey(DocumentsPattern, on_delete=models.CASCADE, null=True, blank=True, related_name='object_document')
    object = models.ForeignKey(Object, on_delete=models.CASCADE, null=True, blank=True, related_name='document_object')
    

