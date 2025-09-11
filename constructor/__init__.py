"""Alias package for re-exporting models and Word API.

This package provides a stable import path for the new document
constructor modules. It re-exports classes and functions from your
existing applications so that the rest of the system can import from
`constructor.models` and `constructor.word_api` without modifying
your current code layout. If you move your models or Word API to
another app, adjust the import statements below accordingly.
"""
