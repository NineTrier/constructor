from django.db import models
from user_manager.models import Organisation

class Object(models.Model):
    name = models.CharField(max_length=255)
    data = models.FileField(upload_to='dataframes/') 
    
    def to_dict(self):
        return {
            'name': self.name,
        }

class Parameter(models.Model):
    object = models.ForeignKey(Object, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    data_type = models.CharField(max_length=255)
    identificator = models.BooleanField(default=False)
    array_separator = models.CharField(max_length=10, blank=True, null=True, default=" ")  # поле для хранения разделителя массива

