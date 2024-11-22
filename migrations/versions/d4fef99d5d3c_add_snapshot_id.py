"""Add snapshot_id

Revision ID: d4fef99d5d3c
Revises: 8fcd403f0c45
Create Date: 2024-11-22 13:00:05.192423

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4fef99d5d3c'
down_revision = '8fcd403f0c45'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('playlist', schema=None) as batch_op:
        batch_op.add_column(sa.Column('snapshot_id', sa.String(length=120), nullable=True))

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('playlist', schema=None) as batch_op:
        batch_op.drop_column('snapshot_id')

    # ### end Alembic commands ###