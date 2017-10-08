from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import sqlalchemy as sa

Base = declarative_base()

__all__ = ['AdminRoom', 'LinkedRoom', 'AuthenticatedUser', 'ServiceUser', 'initialize']


auth_association_table = sa.Table('auth_association', Base.metadata,
                                  sa.Column('room_id', sa.Integer,
                                            sa.ForeignKey('rooms.id')),
                                  sa.Column('user_id', sa.Integer,
                                            sa.ForeignKey('auth_users.id')))

service_association_table = sa.Table('service_association', Base.metadata,
                                     sa.Column('room_id', sa.Integer,
                                               sa.ForeignKey('rooms.id')),
                                     sa.Column(
                                         'user_id', sa.Integer,
                                         sa.ForeignKey('service_users.id')))


class AdminRoom(Base):
    """
    A one-one message between the bridge and a matrix user.
    """
    __tablename__ = "admin_rooms"

    id = sa.Column(sa.Integer, primary_key=True)
    matrix_roomid = sa.Column(sa.String)
    active = sa.Column(sa.Boolean)
    matrix_user = sa.Column(sa.String)


class LinkedRoom(Base):
    """
    A room where the bridge is active.
    """
    __tablename__ = "rooms"

    id = sa.Column(sa.Integer, primary_key=True)
    matrix_roomid = sa.Column(sa.String)
    service_roomid = sa.Column(sa.String)
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
    frontier_userid = sa.Column(
        sa.Integer, sa.ForeignKey("auth_users.id"), nullable=True)
    frontier_user = relationship("AuthenticatedUser")

    def __init__(self, matrix_roomid, service_roomid, active=True):
        self.matrix_roomid = matrix_roomid
        self.service_roomid = service_roomid
        self.active = active


class AuthenticatedUser(Base):
    """
    A User which is authenticated with the Bridge.
    """
    __tablename__ = "auth_users"

    id = sa.Column(sa.Integer, primary_key=True)

    # mxid
    matrixid = sa.Column(sa.String)

    # For single-puppet bot bridges, you might not have a username on the
    # service side.
    serviceid = sa.Column(sa.String, nullable=True)

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
    serviceid = sa.Column(sa.String)
    nick = sa.Column(sa.String)
    matrixid = sa.Column(sa.String)


def initialize(*args, **kwargs):
    """Initializes the database and creates tables if necessary."""
    engine = sa.create_engine(*args, **kwargs)

    Session = sessionmaker()
    Session.configure(bind=engine)
    session = Session()

    Base.metadata.bind = engine
    Base.metadata.create_all()

    return session
