"""Composite FK on enrollments(lea_id, student_id) -> students(lea_id, id).

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-22

Implements the ADR-006 defense-in-depth posture for the highest-volume
join in the rostering canonical: enrollments to students. Before this
migration, enrollments.student_id had a single-column FK to students.id,
and enrollments.lea_id had a single-column FK to leas.id. A bug that
forgot the lea_id filter could in principle join an enrollment in LEA A
to a student in LEA B without producing a database error.

This migration:

1. Adds a UNIQUE constraint on students(lea_id, id) so it can be the
   target of a composite FK. Postgres requires the target columns of
   an FK to have a unique index or constraint.
2. Drops the existing single-column FK on enrollments.student_id
   (Postgres-default-named ``enrollments_student_id_fkey``).
3. Adds a composite FK on enrollments(lea_id, student_id) referencing
   students(lea_id, id). Cross-LEA references are now physically
   impossible at the database layer.

The repository layer remains the primary enforcement per ADR-006. This
FK is the belt-and-suspenders layer that turns a silent join leak into
a database integrity error.

Future student-data tables (iep_snapshot, iep_goal, iep_service,
idea_event when introduced for the Frontline IEP design) should adopt
the same composite-FK pattern.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_students_lea_id",
        "students",
        ["lea_id", "id"],
    )

    op.drop_constraint(
        "enrollments_student_id_fkey",
        "enrollments",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "fk_enrollments_lea_student",
        "enrollments",
        "students",
        ["lea_id", "student_id"],
        ["lea_id", "id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_enrollments_lea_student",
        "enrollments",
        type_="foreignkey",
    )

    op.create_foreign_key(
        "enrollments_student_id_fkey",
        "enrollments",
        "students",
        ["student_id"],
        ["id"],
    )

    op.drop_constraint(
        "uq_students_lea_id",
        "students",
        type_="unique",
    )
