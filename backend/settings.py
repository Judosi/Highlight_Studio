"""Backward-compatible import alias.

Canonical implementation: ``backend.src.highlight_studio.core.settings``.
This file keeps old integrations working while the application uses the
modular package layout under ``backend/src/highlight_studio``.
"""

from importlib import import_module as _import_module
import sys as _sys

_impl = _import_module("backend.src.highlight_studio.core.settings")
_sys.modules[__name__] = _impl
