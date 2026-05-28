#!/usr/bin/env bash
# Roll back all Alembic migrations. Drops every application table; preserves
# the edlink_app, edlink_ops, edlink_dba roles.

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

python -m alembic downgrade base
