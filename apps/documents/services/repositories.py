"""Repository interfaces for ORM models.

The ``ObjectRepository`` encapsulates access to ``Object`` and
``Parameter`` data via Django's ORM and maps them to plain Python
data-transfer objects. This separation allows the domain layer and
other services to work with strongly typed representations without
depending on Django models directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from constructor.models import Object as ObjectModel, Parameter as ParameterModel

__all__ = [
    "ParameterDTO",
    "ObjectDTO",
    "ObjectRepository",
]


@dataclass(frozen=True)
class ParameterDTO:
    """Data-transfer object representing a single parameter.

    Attributes
    ----------
    id: int
        The primary key of the parameter in the database.
    name: str
        The user-visible name of the parameter.
    type: str
        The type of the parameter (e.g. ``TXT``, ``DATE``, ``ARRAY``).
    is_identifier: bool
        Indicates whether this parameter is designated as an identifier
        for records in the associated data object.
    """

    id: int
    name: str
    type: str
    is_identifier: bool


@dataclass
class ObjectDTO:
    """Data-transfer object representing a data object.

    Attributes
    ----------
    id: int
        Primary key of the data object.
    name: str
        The human-friendly name of the data object.
    parameters: list[ParameterDTO]
        A list of parameter descriptors associated with this object.
    """

    id: int
    name: str
    parameters: list[ParameterDTO]


class ObjectRepository:
    """Repository for accessing objects and their parameters.

    Uses Django ORM under the hood but exposes DTOs to the rest of the
    application. This helps enforce the dependency inversion principle.
    """

    def get(self, object_id: int) -> ObjectDTO:
        """Retrieve an object and its parameters by primary key.

        Parameters
        ----------
        object_id: int
            The primary key of the ``Object`` to load.

        Returns
        -------
        ObjectDTO
            A data-transfer object representing the object and its
            parameters.
        """
        obj = ObjectModel.objects.get(id=object_id)
        params = [
            ParameterDTO(
                p.id,
                p.name,
                p.type,
                getattr(p, "identificator", False),
            )
            for p in ParameterModel.objects.filter(object=obj).order_by("id")
        ]
        return ObjectDTO(id=obj.id, name=obj.name, parameters=params)

    def find_by_name(self, name: str) -> Optional[ObjectDTO]:
        """Find an object by name.

        Parameters
        ----------
        name: str
            The name of the object to search for.

        Returns
        -------
        Optional[ObjectDTO]
            The corresponding data-transfer object if found, else
            ``None``.
        """
        try:
            obj = ObjectModel.objects.get(name=name)
            return self.get(obj.id)
        except ObjectModel.DoesNotExist:
            return None
