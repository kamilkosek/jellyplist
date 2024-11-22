"""Add download_Status

Revision ID: a1478d4d2c47
Revises: 30e948342792
Create Date: 2024-10-25 19:37:41.898682

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1478d4d2c47'
down_revision = '30e948342792'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('track', schema=None) as batch_op:
        batch_op.add_column(sa.Column('download_status', sa.String(length=200), nullable=True))

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('track', schema=None) as batch_op:
        batch_op.drop_column('download_status')

    # ### end Alembic commands ###