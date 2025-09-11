"""Alias module re-exporting models for the document constructor.

This module makes it possible to import your existing Django models via
``from constructor import models`` or ``from constructor.models import <Model>``.

It simply imports the relevant classes from your existing app and re-exports
them under the same names. By default this file assumes that your current
models live in an app called ``document`` with a ``models.py`` that defines
classes like ``DocumentsPattern``, ``DocumentPattern_Objects``, ``Object``
(often aliased to ``ObjectModel``), ``Parameter`` and ``CreatedDocument``.

If your models live in a different module or are named differently, update
the import statements below accordingly.
"""

try:
    # Adjust this import to point to where your actual models live.
    from document.models import (
        DocumentsPattern,
        DocumentPattern_Objects,
        Object as ObjectModel,
        Parameter,
        CreatedDocument,
    )
except ImportError as exc:
    # Provide a helpful error message if the import fails.
    raise ImportError(
        "Failed to import models from your project. "
        "Update constructor/models.py to import models from the correct app."
    ) from exc

# Re-export the imported names for consumers of this module
__all__ = [
    "DocumentsPattern",
    "DocumentPattern_Objects",
    "ObjectModel",
    "Parameter",
    "CreatedDocument",
]