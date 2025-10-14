"""
Modified models for the `database_manager` app.

This version keeps the original intent of simplifying data storage for
non‑SQL users while improving the way objects and their records are
linked. It adds uniqueness constraints to prevent duplicate links and
renames some fields for clarity.  The underlying concept – storing
uploaded CSV data as pickled pandas DataFrames – remains unchanged.

Note: migrations would need to be created for these changes when
integrating into a real Django project.
"""

from django.db import models
import dateutil.parser


class Object(models.Model):
    """Represents a user-defined table stored as a pickled DataFrame."""

    name = models.CharField(max_length=255)
    # FileField pointing at a pickled DataFrame in MEDIA_ROOT/dataframes/
    data = models.FileField(upload_to="dataframes/")

    class Meta:
        ordering = ["name"]
        permissions = (
            ("manage_object_structure", "Can manage object structure"),
            ("manage_object_data", "Can manage object data"),
            ("manage_object_links", "Can manage object links"),
        )

    def to_dict(self) -> dict:
        """Return a minimal dictionary representation used by API responses."""
        return {"name": self.name}

    def __str__(self) -> str:
        return self.name


class ParameterCategory(models.Model):
    """
    A category or group of parameters for an Object.  Categories allow
    parameters to be grouped on forms and arranged in a user‑defined order.

    Each category belongs to a specific Object and has an explicit order
    value to control the vertical ordering of categories on data entry forms.
    """

    object = models.ForeignKey(Object, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    order = models.IntegerField(default=0)

    class Meta:
        unique_together = ("object", "name")
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return f"{self.object.name} :: {self.name}"


class Parameter(models.Model):
    """Describes a column (field) in a user‑defined object."""

    # link back to the parent object
    object = models.ForeignKey(Object, on_delete=models.CASCADE)
    # optional link to a category; if null, the parameter will appear in a
    # default unnamed group
    category = models.ForeignKey(ParameterCategory, on_delete=models.SET_NULL, null=True, blank=True)
    # display name of the column
    name = models.CharField(max_length=255)
    # type of data stored in the column
    data_type = models.CharField(max_length=255)
    # whether this parameter is the primary identifier for records
    identificator = models.BooleanField(default=False)
    # delimiter used for ARRAY types; optional
    array_separator = models.CharField(max_length=10, blank=True, null=True, default=" ")
    # optional date format string for DATE types
    date_format = models.CharField(max_length=255, blank=True, null=True)
    # ordering of the parameter within its category
    order = models.IntegerField(default=0)
    # optional link to a child object for parameters that represent object links
    linked_object = models.ForeignKey(Object, on_delete=models.SET_NULL, null=True, blank=True, related_name='link_parameters')

    class Meta:
        ordering = ["category__order", "order", "id"]

    def __str__(self) -> str:
        return f"{self.object.name} -> {self.name}"

    def parse_date(self, date_str: str) -> str:
        """
        Parse an incoming date string using the configured format.  If the
        parameter is not of type "DATE" or parsing fails, the original
        string is returned.
        """
        DATE_FORMATS = {
            "DD.MM.YYYY": "%d.%m.%Y",
            "MM/DD/YYYY": "%m/%d/%Y",
            "DD MMMM YYYY года в HH часов MM минут": "%d %B %Y года в %H часов %M минут",
            # add additional formats as required
        }
        month_names = {
            "January": "января",
            "February": "февраля",
            "March": "марта",
            "April": "апреля",
            "May": "мая",
            "June": "июня",
            "July": "июля",
            "August": "августа",
            "September": "сентября",
            "October": "октября",
            "November": "ноября",
            "December": "декабря",
        }
        if self.data_type != "DATE":
            return date_str
        if not self.date_format:
            return date_str
        try:
            date = dateutil.parser.parse(date_str, fuzzy=True)
            str_date = date.strftime(DATE_FORMATS[self.date_format])
            # localise month names
            str_date = str_date.replace(date.strftime("%B"), month_names.get(date.strftime("%B"), date.strftime("%B")))
            return str_date
        except Exception:
            return date_str


class Object_ParentObject(models.Model):
    """
    Stores a high‑level relationship between two objects.  The
    ``parent_object`` is considered the master, and the ``object`` is a
    child.  A unique constraint ensures that the same pair cannot be
    linked twice.
    """

    object = models.ForeignKey(
        Object,
        on_delete=models.CASCADE,
        related_name="child_links",
    )
    parent_object = models.ForeignKey(
        Object,
        on_delete=models.CASCADE,
        related_name="parent_links",
    )

    # Defines whether the relationship between the parent and child
    # objects allows linking a single record or multiple records from
    # the child.  "single" corresponds to a one‑to‑one style link,
    # whereas "multiple" corresponds to one‑to‑many.
    LINK_TYPE_CHOICES = [
        ("single", "Один элемент"),
        ("multiple", "Несколько элементов"),
    ]
    link_type = models.CharField(
        max_length=10,
        choices=LINK_TYPE_CHOICES,
        default="single",
    )

    class Meta:
        unique_together = ("object", "parent_object")

    def __str__(self) -> str:
        return f"{self.parent_object} -> {self.object}"


class ObjectLink_identificators(models.Model):
    """
    Stores row‑level links between parent and child objects.  Each entry
    links a record (identified by ``parent_object_identificator``) in the
    parent object to a record (``object_identificator``) in the child
    object.  A unique constraint ensures the same mapping cannot be
    duplicated.
    """

    object_link = models.ForeignKey(
        Object_ParentObject,
        on_delete=models.CASCADE,
        related_name="row_links",
    )
    # identifier (id_to_connect) of a row in the child object
    object_identificator = models.CharField(max_length=255)
    # identifier (id_to_connect) of a row in the parent object
    parent_object_identificator = models.CharField(max_length=255)

    class Meta:
        unique_together = ("object_link", "object_identificator", "parent_object_identificator")

    def __str__(self) -> str:
        return f"{self.object_link}: {self.parent_object_identificator} -> {self.object_identificator}"
