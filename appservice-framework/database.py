from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import sqlalchemy as sa

engine = None
Base = declarative_base()
Session = sessionmaker()
session = None


class AdminRoom(Base):
    """
    A one-one message between the bridge and a matrix user.
    """
    __table__name = "admin_rooms"

    id = sa.Column(sa.Integer, primary_key=True)
    matrix_room_id = sa.Column(sa.String)
    active = sa.Column(sa.Boolean)
    matrix_user = sa.Column(sa.string)


auth_association_table = sa.Table('association', Base.metadata,
                                  sa.Column('room_id', sa.Integer,
                                            sa.ForeignKey('rooms.id')),
                                  sa.Column('user_id', sa.Integer,
                                            sa.ForeignKey('auth_users.id')))

service_association_table = sa.Table('association', Base.metadata,
                                     sa.Column('room_id', sa.Integer,
                                               sa.ForeignKey('rooms.id')),
                                     sa.Column(
                                         'user_id', sa.Integer,
                                         sa.ForeignKey('service_users.id')))


class LinkedRoom(Base):
    """
    A room where the bridge is active.
    """
    __tablename__ = "rooms"

    id = sa.Column(sa.Integer, primary_key=True)
    matrix_room_id = sa.Column(sa.String)
    service_room = sa.Column(sa.String)
    active = sa.Column(sa.Boolean)

    # Maintain a list of all auth users in this room
    auth_users = relationship(
        "AuthenticatedUser",
        secondary=auth_association_table,
        back_populates="rooms")

    # Maintain a list of all service users in this room.
    # This relationship is not bi-directional
    service_users = relationship(
        "ServiceUser", secondary=service_association_table)

    # Know which user to listen to events from
    frontier_user_id = sa.Column(
        sa.Integer, sa.ForigenKey("auth_users.id"), nullable=True)
    frontier_user = relationship("AuthenticatedUser")

    def __init__(self, matrix_room, service_room, active=True):
        self.matrix_room = matrix_room
        self.service_room = service_room
        self.active = active


class AuthenticatedUser(Base):
    """
    A User which is authenticated with the Bridge.
    """
    __tablename__ = "auth_users"

    id = sa.Column(sa.Integer, primary_key=True)

    # mxid
    matrix_id = sa.Column(sa.String)

    # For single-puppet bot bridges, you might not have a username on the
    # service side.
    service_id = sa.Column(sa.String, nullable=True)

    # Full Name
    nick = sa.Column(sa.String)

    # User name and "password"
    service_username = sa.Column(sa.String, nullable=True)
    auth_token = sa.Column(sa.String, nullable=True)

    rooms = relationship(
        "AuthenticatedUser",
        secondary=auth_association_table,
        back_populates="auth_users")


class ServiceUser(Base):
    """
    A User that only exists on the Service Side.
    """
    __tablename__ = "service_users"

    id = sa.Column(sa.Integer, primary_key=True)
    user_id = sa.Column(sa.String)
    nick = sa.Column(sa.String)
    mxid = sa.Column(sa.String)
