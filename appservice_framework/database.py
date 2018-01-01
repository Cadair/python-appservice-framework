from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import sqlalchemy as sa

Base = declarative_base()

__all__ = ['Room', 'LinkedRoom', 'User', 'AuthenticatedUser', 'initialize']


room_user_table = sa.Table('user_association', Base.metadata,
                           sa.Column('roomid', sa.Integer,
                                     sa.ForeignKey('room.id')),
                           sa.Column('userid', sa.Integer,
                                     sa.ForeignKey('user.id')))

class Room(Base):
    """
    A Matrix room.
    """
    __tablename__ = "room"

    id = sa.Column(sa.Integer, primary_key=True)
    type = sa.Column(sa.String)
    __mapper_args__ = {
        'polymorphic_identity': 'admin',
        'polymorphic_on': type
    }
    matrixid = sa.Column(sa.String)
    active = sa.Column(sa.Boolean)

    users = relationship(
        "User",
        secondary=room_user_table,
        back_populates="rooms")

    # Know which user to listen to events from
    frontier_userid = sa.Column(
        sa.Integer, sa.ForeignKey("auth_user.id"), nullable=True)
    frontier_user = relationship("AuthenticatedUser")

    def __init__(self, matrixid, active=True):
        self.matrixid = matrixid
        self.active = active

    @property
    def auth_users(self):
        return list(filter(lambda x: isinstance(x, AuthenticatedUser), self.users))


class LinkedRoom(Room):
    """
    A Matrix room linked to a service room.
    """
    __tablename__ = "linked_room"
    __mapper_args__ = {
        'polymorphic_identity': 'bridged',
    }

    id = sa.Column(sa.Integer, sa.ForeignKey('room.id'), primary_key=True)
    serviceid = sa.Column(sa.String)


    def __init__(self, matrixid, serviceid, active=True):
        super().__init__(matrixid, active=active)
        self.serviceid = serviceid


class User(Base):
    """
    A user that exists in both matrix and the service.
    """
    __tablename__ = "user"

    id = sa.Column(sa.Integer, primary_key=True)
    type = sa.Column(sa.String)

    __mapper_args__ = {
        'polymorphic_identity': 'service',
        'polymorphic_on': type
    }

    nick = sa.Column(sa.String, nullable=True)
    serviceid = sa.Column(sa.String)
    matrixid = sa.Column(sa.String)

    rooms = relationship(
        "Room",
        secondary=room_user_table,
        back_populates="users")


    def __init__(self, matrixid, serviceid, nick=None):
        self.nick = nick
        self.serviceid = serviceid
        self.matrixid = matrixid


class AuthenticatedUser(User):
    """
    A user that is authenticated with the AS for the service. These users are
    the only ones that can send and receive messages from the service.
    """
    __tablename__ = "auth_user"
    __mapper_args__ = {
        'polymorphic_identity': 'auth',
    }

    id = sa.Column(sa.Integer, sa.ForeignKey("user.id"), primary_key=True)
    auth_token = sa.Column(sa.String, nullable=True)


    def __init__(self, matrixid, serviceid, auth_token, nick=None):
        super().__init__(matrixid, serviceid, nick)
        self.auth_token = auth_token


def initialize(*args, **kwargs):
    """Initializes the database and creates tables if necessary."""
    engine = sa.create_engine(*args, **kwargs)

    Session = sessionmaker()
    Session.configure(bind=engine)
    session = Session()

    Base.metadata.bind = engine
    Base.metadata.create_all()

    return session
