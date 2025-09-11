"""Alias module re-exporting the Word API for the document constructor.

Many parts of the refactored constructor code expect to import the class
responsible for working with Word/DocX files from ``constructor.word_api``.

This file imports that class from your existing application and re-exports
it. By default it assumes your project defines ``Document`` in
``document.word_api``. If the class lives elsewhere, adjust the import
below to point to the correct module.
"""

try:
    # Adjust this import to point to where your Word API (Document) class lives.
    from document.Document import Document
except ImportError as exc:
    raise ImportError(
        "Failed to import Document class from your project. "
        "Update constructor/word_api.py to import from the correct module."
    ) from exc

# Re-export the imported name
__all__ = ["Document"]