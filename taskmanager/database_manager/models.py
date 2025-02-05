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

class Parameter(models.Model):
    object = models.ForeignKey(Object, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    data_type = models.CharField(max_length=255)
    identificator = models.BooleanField(default=False)
    array_separator = models.CharField(max_length=10, blank=True, null=True, default=" ")  # поле для хранения разделителя массива
    date_format = models.CharField(max_length=255, blank=True, null=True)
    
    def parse_date(self, date_str):
        DATE_FORMATS = {
            'DD.MM.YYYY': '%d.%m.%Y',
            'MM/DD/YYYY': '%m/%d/%Y',
            # добавить другие форматы
        }
        if self.data_type != "DATE":
            return date_str
        print('##############', date_str)
        if self.date_format:
            try:
                print('##################', self.date_format)
                date = dateutil.parser.parse(date_str, fuzzy=True)
                print(date)
                str_date = date.strftime(DATE_FORMATS[self.date_format])
                print(str_date)
                return str_date
            except ValueError:
                return date_str
