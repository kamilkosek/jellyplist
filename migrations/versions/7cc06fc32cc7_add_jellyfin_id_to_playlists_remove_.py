"""Add jellyfin_id to Playlists, remove jellyfin_id from user_playlists

Revision ID: 7cc06fc32cc7
Revises: 2b8f75ed0a54
Create Date: 2024-10-22 17:24:39.761794

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7cc06fc32cc7'
down_revision = '2b8f75ed0a54'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('playlist', schema=None) as batch_op:
        batch_op.add_column(sa.Column('jellyfin_id', sa.String(length=120), nullable=True))

    with op.batch_alter_table('user_playlists', schema=None) as batch_op:
        batch_op.drop_column('jellyfin_id')

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('user_playlists', schema=None) as batch_op:
        batch_op.add_column(sa.Column('jellyfin_id', sa.VARCHAR(length=120), autoincrement=False, nullable=True))

    with op.batch_alter_table('playlist', schema=None) as batch_op:
        batch_op.drop_column('jellyfin_id')

    # ### end Alembic commands ###
