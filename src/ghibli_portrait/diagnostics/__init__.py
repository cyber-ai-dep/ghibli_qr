"""Operational diagnostics: request correlation, in-memory log buffer, runtime snapshot.

This package is deliberately self-contained and imports nothing from the rest of
the application at module level. `main.py` installs the request-id filter and the
log buffer BEFORE any project module is imported, so anything imported from here
must be safe to load that early.
"""
