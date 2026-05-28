"""Allow ``python -m edlink_rostering`` to dispatch the operator CLI.

Keeps the same surface as the ``edlink-rostering`` console script defined in
``pyproject.toml`` so the CLI works whether the project is installed via
``pip install -e .`` or invoked as a module.
"""

from __future__ import annotations

from edlink_rostering.cli import cli


if __name__ == "__main__":
    cli()
