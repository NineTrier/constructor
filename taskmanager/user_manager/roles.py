from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError, OperationalError, ProgrammingError


ROLE_GROUP_PREFIX = "role__"


@dataclass(frozen=True)
class RoleDefinition:
    code: str
    name: str
    permissions: Sequence[str]
    description: str = ""

    @property
    def group_name(self) -> str:
        return f"{ROLE_GROUP_PREFIX}{self.code}"


ROLE_DEFINITIONS: Tuple[RoleDefinition, ...] = (
    RoleDefinition(
        code="objects_admin",
        name="Менеджер объектов",
        permissions=(
            "database_manager.view_object",
            "database_manager.add_object",
            "database_manager.change_object",
            "database_manager.delete_object",
            "database_manager.manage_object_structure",
            "database_manager.manage_object_data",
            "database_manager.manage_object_links",
            "database_manager.add_parameter",
            "database_manager.change_parameter",
            "database_manager.delete_parameter",
            "database_manager.view_parameter",
        ),
    ),
    RoleDefinition(
        code="objects_editor",
        name="Редактор данных объектов",
        permissions=(
            "database_manager.view_object",
            "database_manager.manage_object_data",
            "database_manager.add_parameter",
            "database_manager.change_parameter",
            "database_manager.view_parameter",
        ),
    ),
    RoleDefinition(
        code="objects_viewer",
        name="Просмотр объектов",
        permissions=("database_manager.view_object",),
    ),
    RoleDefinition(
        code="documents_manager",
        name="Менеджер документов",
        permissions=(
            "document.view_documentspattern",
            "document.add_documentspattern",
            "document.change_documentspattern",
            "document.delete_documentspattern",
            "document.view_savedelements",
            "document.add_savedelements",
            "document.change_savedelements",
            "document.delete_savedelements",
        ),
    ),
    RoleDefinition(
        code="documents_org_manager",
        name="Менеджер документов организации",
        permissions=(
            "document.view_documentspattern",
            "document.toggle_document_organisation",
        ),
    ),
)


def role_choices() -> List[Tuple[str, str]]:
    return [(role.code, role.name) for role in ROLE_DEFINITIONS]


def get_role_definition(code: str) -> Optional[RoleDefinition]:
    return next((role for role in ROLE_DEFINITIONS if role.code == code), None)


def get_role_group(code: str) -> Optional[Group]:
    role = get_role_definition(code)
    if role is None:
        return None
    try:
        return Group.objects.get(name=role.group_name)
    except Group.DoesNotExist:
        return None


def _collect_permissions(permission_codes: Iterable[str]) -> List[Permission]:
    permissions: List[Permission] = []
    for perm in permission_codes:
        try:
            app_label, codename = perm.split(".", 1)
        except ValueError:
            continue
        try:
            perm_obj = Permission.objects.get(content_type__app_label=app_label, codename=codename)
        except Permission.DoesNotExist:
            continue
        permissions.append(perm_obj)
    return permissions


def ensure_roles_exist() -> None:
    try:
        for role in ROLE_DEFINITIONS:
            group, _ = Group.objects.get_or_create(name=role.group_name)
            permissions = _collect_permissions(role.permissions)
            group.permissions.set(permissions)
    except (OperationalError, ProgrammingError, IntegrityError):
        # База может быть не готова (например, до применения миграций).
        return
