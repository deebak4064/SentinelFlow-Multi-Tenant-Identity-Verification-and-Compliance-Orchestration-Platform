"""indexes found in the principal review: risk list sorted by score, case queue sorted by SLA

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_risk_org_score", "risk_assessments", ["org_id", "score"])
    op.create_index("ix_cases_org_sla", "compliance_cases", ["org_id", "sla_due_at"])


def downgrade() -> None:
    op.drop_index("ix_cases_org_sla", table_name="compliance_cases")
    op.drop_index("ix_risk_org_score", table_name="risk_assessments")
