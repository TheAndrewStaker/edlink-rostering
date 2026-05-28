"""Operator CLI commands.

Each command is its own module under this package. The top-level
``edlink_rostering.cli`` module imports and registers them on the click group.
Splitting commands keeps each file scoped to one operation and prevents
the kind of unbounded growth that the global file-scope rule warns
against.
"""
