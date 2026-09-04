"""Core engine package.

Intentionally light: importing :mod:`universal_computer.core` must not pull in
the whole engine, because low-level modules (e.g. ``core.errors``) are used by
``config`` and ``logging``. Import submodules directly.
"""
