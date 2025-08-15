from django.db import models
from user_manager.models import Organisation
import dateutil.parser

class Object(models.Model):
    name = models.CharField(max_length=255)
    data = models.FileField(upload_to='dataframes/') 
    
    def to_dict(self):
        return {
            'name': self.name,
        }
    
    def __str__(self):
        return self.name

class Parameter(models.Model):
    object = models.ForeignKey(Object, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    data_type = models.CharField(max_length=255)
    identificator = models.BooleanField(default=False)
    array_separator = models.CharField(max_length=10, blank=True, null=True, default=" ")  # поле для хранения разделителя массива
    date_format = models.CharField(max_length=255, blank=True, null=True)
    
    def __str__(self):
        return f"{self.object.name} -> {self.name}"
    
    def parse_date(self, date_str):
        DATE_FORMATS = {
            'DD.MM.YYYY': '%d.%m.%Y',
            'MM/DD/YYYY': '%m/%d/%Y',
            'DD MMMM YYYY года в HH часов MM минут':'%d %B %Y года в %H часов %M минут'
            # добавить другие форматы
        }
        month_names = {
            'January': 'января',
            'February': 'февраля',
            'March': 'марта',
            'April': 'апреля',
            'May': 'мая',
            'June': 'июня',
            'July': 'июля',
            'August': 'августа',
            'September': 'сентября',
            'October': 'октября',
            'November': 'ноября',
            'December': 'декабря'
        }
        if self.data_type != "DATE":
            return date_str
        print('##############', date_str)
        if self.date_format:
            try:
                print('##################', self.date_format)
                print(date_str)
                date = dateutil.parser.parse(date_str, fuzzy=True)
                print(date)
                str_date = date.strftime(DATE_FORMATS[self.date_format])
                str_date = str_date.replace(date.strftime('%B'), month_names[date.strftime('%B')])
                print(str_date)
                return str_date
            except ValueError:
                return date_str


class Object_ParentObject(models.Model):
    object = models.ForeignKey(Object, on_delete=models.CASCADE, related_name='object', blank=True, null=True)
    parent_object = models.ForeignKey(Object, on_delete=models.CASCADE, related_name='parent_object', blank=True, null=True)
    
class ObjectLink_identificators(models.Model):
    object_link = models.ForeignKey(Object_ParentObject, on_delete=models.CASCADE, related_name='object_link', blank=True, null=True)
    object_identificator = models.CharField(max_length=255, blank=True, null=True)
    parent_object_identificator = models.CharField(max_length=255, blank=True, null=True)