import json
import asyncio
import logging
from collections import namedtuple
from contextlib import contextmanager
from functools import wraps, partial
from urllib.parse import quote

import aiohttp
import aiohttp.web

from matrix_client.errors import MatrixRequestError

from . import database as db
from .matrix_api import AsyncHTTPAPI as MatrixAPI

log = logging.getLogger("appservice_framework")

handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s %(name)-12s %(levelname)-8s %(message)s')
handler.setFormatter(formatter)

log.addHandler(handler)
log.setLevel(logging.INFO)

__all__ = ['AppService']


config = namedtuple("config", "invite_only_rooms")

class AppService:
    """
    Run the Matrix Appservice.

    This needs to maintain state of matrix rooms and bridged users in those rooms.
    """

    def __init__(self, matrix_server, server_domain, access_token,
                 user_namespace, room_namespace, database_url,
                 loop=None, invite_only_rooms=False):

        if loop:
            self.loop = loop
        else:
            self.loop = asyncio.get_event_loop()

        self._http_session = None
        self._api = None

        self.matrix_server = matrix_server
        self.access_token = access_token
        self.server_name = server_domain
        self.user_namespace = user_namespace
        self.room_namespace = room_namespace

        self.config = config(invite_only_rooms=invite_only_rooms)

        self.dbsession = db.initialize(database_url)

        # Setup web server to listen for appservice calls
        self.app = aiohttp.web.Application(loop=self.loop)
        self._routes()

        # Setup internal matrix event dispatch
        self._matrix_event_mapping()
        self.matrix_events = {}
        self.service_events = {}

        # Keep a mapping of service connections
        self.service_connections = {}

    @property
    def http_session(self):
        """
        The HTTP session object.

        .. note::
            This is only acceible inside the `~appservice_framework.AppService.run` context manager.
        """
        if self._http_session is None:
            raise AttributeError("the http_session attribute can only be used "
                                 "from within the `AppService.run` context manager")
        else:
            return self._http_session

    @property
    def api(self):
        """
        The matrix API object for the appservice.

        .. note::
            This is only acceible inside the `~appservice_framework.AppService.run` context manager.
        """
        if self._api is None:
            raise AttributeError("the run attribute can only be used from "
                                 "within the `AppService.run` context manager")
        else:
            return self._api

    def _make_async(self, call):
        """
        Wrap a function in a coroutine
        """
        if not asyncio.iscoroutinefunction(call):
            @wraps(call)
            async def caller(*args, **kwargs):
                return call(*args, **kwargs)

            return caller

        else:
            return call

    ######################################################################################
    # Appservice Web Server Handles
    ######################################################################################

    @contextmanager
    def run(self, host="127.0.0.1", port=5000):
        """
        Run the appservice.

        Example
        -------

        >>> apps = AppService(...)
        >>> with apps.run() as run_forever:
        ...     run_forever()

        """
        self._http_session = aiohttp.ClientSession(loop=self.loop)
        self._api = MatrixAPI(self.matrix_server, self.http_session, self.access_token)

        for user in self.dbsession.query(db.AuthenticatedUser):
            if user not in self.service_connections:
                connection = asyncio.ensure_future(
                    self.service_events['connect'](self, user.serviceid, user.auth_token))
                if connection:
                    self.service_connections[user] = connection

        # TODO: This should manually start the webapp.
        # The object yielded here should have a `run_forever` method that actually blocks.
        # Rather than making the context manager block.
        yield partial(aiohttp.web.run_app, self.app, host=host, port=port)

        for connection in self.service_connections.values():
            if hasattr(connection, "close"):
                connection.close()

        self._api = None
        self._http_session.close()
        self._http_session = None

    def _routes(self):
        """
        Add route handlers to the web server.
        """
        self.app.router.add_route('PUT', "/transactions/{transaction}",
                                  self._recieve_matrix_transaction)
        self.app.router.add_route('GET', "/rooms/{alias}", self._room_alias)
        self.app.router.add_route('GET', "/users/{userid}", self._query_userid)

    async def _recieve_matrix_transaction(self, request):
        """
        Receive an Appservice push matrix event.
        """
        json = await request.json()
        events = json["events"]
        for event in events:
            meth = self._matrix_event_dispatch.get(event['type'], None)
            if meth:
                try:
                    await meth(event)
                except Exception as e:
                    return aiohttp.web.Response(staus=500)

        return aiohttp.web.Response(body=b"{}")

    async def _room_alias(self, request):
        """
        Handle an Appservice room_alias call.
        """
        return aiohttp.web.Response(status=200, body=b"{}")
        alias = request.match_info["alias"]
        room = self.get_room(matrixid=alias)

        if room:
            log.debug("room found")
            return aiohttp.web.Response(status=200, body=b"{}")

        return aiohttp.web.Response(status=404)

    async def _query_userid(self, request):
        """
        Handle an Appservice userid call.
        """
        return aiohttp.web.Response(status=404)

    ######################################################################################
    # Internal Matrix Handlers
    ######################################################################################

    def _matrix_event_mapping(self):
        """
        Define a event['type'] -> method mapping.
        """
        self._matrix_event_dispatch = {
            'm.room.member': self._matrix_membership_change,
            'm.room.message': self._matrix_message
        }

    async def _matrix_membership_change(self, event):
        # TODO: If an invite to a room we don't know about
        # TODO: If a direct chat invite (for admin room).
        # TODO: If a leave event in a bridged room.
        # TODO: If a join event in a bridged room.
        pass

    async def _matrix_message(self, event):
        # TODO: If a message in a bridged room.
        # TODO: If a message in an admin room.
        log.debug(event)

    ######################################################################################
    # Matrix Event Decorators
    ######################################################################################

    def matrix_recieve_message(self, coro):
        """
        A matrix 'm.room.message' event in a bridged room.

        coro(appservice, event)
        """
        self.matrix_events['recieve_message'] = coro

        return coro

    def matrix_user_join(self, coro):
        """
        coro(appservice, event)
        """
        self.matrix_events['user_join'] = coro

        return coro

    def matrix_user_part(self, coro):
        """
        coro(appservice, event)
        """
        self.matrix_events['user_part'] = coro

        return coro

    def matrix_user_typing(self, coro):
        """
        coro(appservice, event)
        """
        self.matrix_events['user_typing'] = coro

        return coro

    # TODO: Add matrix state (online/offline)
    # TODO: Add matrix user read
    # TODO: Add matrix m.emote
    # TODO: Add matrix m.image
    # TODO: Add a hook for plain text or html messages?

    ######################################################################################
    # Service Event Decorators
    ######################################################################################

    def service_connect(self, coro):
        """
        Connect to the service.

        coro(appservice, serviceid, auth_token)

        Returns:
            `service` an object representing the connection, becomes ``appservice.service``.
        """
        coro = self._make_async(coro)

        self.service_events['connect'] = coro
        return coro

    def service_room_exists(self, coro):
        """
        Decorator to query if a service room exists.

        coro(appservice, service_roomid)
        """
        self.service_events['room_exists'] = coro

        return coro

    def service_join_room(self, coro):
        """
        Decorator for when an authenticated user joins a room.

        coro(appservice)

        Returns:
            service_userid : `str`
            servicce_roomid : `str`
        """

        async def join_room(self):
            userid, roomid = await coro(self)

            # TODO: Perform matrix side stuff

        self.service_events['join_room'] = join_room

        return join_room

    def service_part_room(self, coro):
        """
        Decorator for when an authenticated user leaves a room.

        coro(appservice)

        Returns:
            matrix_mxid : `str`
            matrix_room_alias : `str`
        """
        async def part_room(self):
            mxid, room_alias = await coro(self)

            # TODO: Perform matrix side stuff

        self.service_events['part_room'] = part_room

        return part_room

    def service_change_profile_image(self, coro):
        """
        Decorator for when an authenticated user changes profile picture

        coro(appservice)

        Returns:
            mxid
            image_url
            force_update
        """

        async def profile_image(self):
            user_id, image_url, force = await coro(self)

            resp = await self.set_matrix_profile_image(user_id, image_url, force)

            return resp

        self.service_events['profile_image'] = profile_image

        return profile_image

    ######################################################################################
    # Matrix Helper Functions
    ######################################################################################

    async def get_room_id(self, room_alias):
        """
        Given a matrix room alias, lookup a room id.

        Parameters
        ----------

        room_alias : `str`
            The room alias to lookup the room id for.
        """
        room_alias = quote(room_alias)
        json = await self.api._send("GET", "/directory/room/{}".format(room_alias))
        if 'room_id' in json:
            return json['room_id']

    async def matrix_send_message(self, user, room, message):
        """
        Send a message to a matrix room as a matrix user.

        Parameters
        ----------

        user : `appservice_framework.database.User`
            The user to send the message as.

        room : `appservice_framework.database.Room`
            The Room to send the message to.

        message : `str`
            The message to send.
        """
        mxid = user.matrixid
        roomid = await self.get_room_id(room.matrixid)
        return self.api.send_message(roomid,
                                     message,
                                     query_params={'user_id': mxid})

    async def create_matrix_user(self, service_userid, matrix_userid=None, matrix_roomid=None):
        """
        Create a matrix user within the appservice namespace for a service user.

        Parameters
        ----------
        service_userid : `str`
            The service id of the user.

        matrix_userid : `str`, optional
            The matrix userid of the user. If not specified one will be created
            following the template ``{prefix}{service_userid}{server_name}``
            where ``prefix`` is based on the appservice user namespace.

        Returns
        -------

        user : `~appservice_framework.database.User`
            The user which was created.
        """

        user = self.dbsession.query(db.User).filter(db.User.serviceid == service_userid).one_or_none()
        if user:
            return user

        prefix = self.user_namespace.split(".*")[0]
        if not matrix_userid:
            matrix_userid = f"{prefix}{service_userid}:{self.server_name}"

        # Localpart is everything before : without #
        localpart = matrix_userid.split(':')[0][1:]

        user = db.User(matrix_userid, service_userid)
        self.dbsession.add(user)


        self.dbsession.commit()

        data = {
            'type': "m.login.application_service",
            'username': quote(localpart)
        }

        try:
            resp = await self.api._send(
                "POST",
                path="/register",
                query_params={"access_token": self.api.token},
                content=data)

        # Catch if this AS user has already been registered
        except MatrixRequestError as e:
            content = json.loads(e.content)
            if content['errcode'] != "M_USER_IN_USE":
                raise e


        return user

    async def set_matrix_profile_image(self, user_id, image_url, force=False):
        """
        Set the profile image for a matrix user.
        """
        if force or not await self.matrix_client.get_avatar_url(user_id) and image_url:
            # Download profile picture
            async with self.http_session.request("GET", image_url) as resp:
                data = await resp.read()

            # Upload to homeserver
            resp = await self.matrix_client.media_upload(data, resp.content_type,
                                                         user_id=user_id)
            json = await resp.json()
            avatar_url = json['content_uri']

            # Set profile picture
            resp = await self.matrix_client.set_avatar_url(user_id, avatar_url)

            return resp

    ######################################################################################
    # Public Appservice Methods
    ######################################################################################

    def get_connection(self, serviceid=None, wait_for_connect=False):
        """
        Get the connection object for a given user.

        Parameters
        ----------
        serviceid : `str`
            The service user id for the connection.

        wait_for_connect : `bool`, optional, default: `False`
            If `True` this function will block until the connection is made, if
            `False` it will return the `asyncio.Task` object for the connection
            attempt.

        Returns
        -------
        connection : `object` or `asyncio.Task`
            The connection object as returned by ``@appservice.service_connect``.

        """

        if not serviceid:
            if len(self.service_connections) > 1:
                raise ValueError("serviceid must be specified if there are more than one connection.")
            else:
                connection = list(self.service_connections.values())[0]
        else:
            authuser = self.dbsession.query(db.User).filter(User.serviceid == serviceid)
            connection = self.service_connections[authuser]

        if wait_for_connect:
            return self.loop.run_until_complete(connection)
        else:
            return connection

    def get_user(self, matrixid=None, serviceid=None):
        """
        Get a `appservice_framework.database.User` object based on IDs.

        Parameters
        ----------

        matrixid : `str`, optional
            The matrix id of the user to lookup.

        serviceid : `str`, optional
            Ther service id of the user to lookup.

        Returns
        -------

        user : `appservice_framework.database.User` or `None`
            The user in the database or `None` if the user was not found.
        """
        if not (matrixid or serviceid):
            raise ValueError("Either matrixid or serviceid must be specified.")

        if matrixid:
            filterexp = db.User.matrixid == matrixid
        if serviceid:
            filterexp = db.User.serviceid = serviceid

        return self.dbsession.query(db.User).filter(filterexp).one_or_none()

    def get_room(self, matrixid=None, serviceid=None):
        """
        Get a `appservice_framework.database.Room` object based on IDs.

        Parameters
        ----------

        matrixid : `str`, optional
            The matrix id of the room to lookup.

        serviceid : `str`, optional
            Ther service id of the room to lookup.

        Returns
        -------

        user : `appservice_framework.database.Room` or `None`
            The user in the database or `None` if the room was not found.
        """
        if not (matrixid or serviceid):
            raise ValueError("Either matrixid or serviceid must be specified.")

        if matrixid:
            filterexp = db.Room.matrixid == matrixid
        if serviceid:
            filterexp = db.Room.serviceid = serviceid

        return self.dbsession.query(db.Room).filter(filterexp).one_or_none()

    async def relay_service_message(self, service_userid, service_roomid, message, receiving_serviceid=None):
        """
        Forward a message to matrix.

        Parameters
        ----------
        service_userid : `str`
            Service User ID

        service_roomid : `str`
            Service Room ID

        message : `str`
            Message to relay.

        receiving_serviceid : `str`
            The service user id of the receiving account. Can be `None`
            if there is only one authenticated user in the room.a

        Returns
        -------

        response `None` or Response
            Returns None if the receiving user is not the frontier user,
            otherwise returns the response from the matrix send message call.

        """
        # TODO: Handle plain/HTML/markdown

        room = self.dbsession.query(db.LinkedRoom).filter(db.LinkedRoom.serviceid == service_roomid).one_or_none()
        if not room:
            raise ValueError("No linked room exists for the service room {}.".format(service_roomid))

        # receiving_serviceid is needed if there is more than one auth user in a room.
        if not receiving_serviceid and len(room.auth_users) > 1:
            raise ValueError("If there is more than one "
                             "AuthenticatedUser in the room, then receiving_serviceid "
                             "must be specified.")
        elif receiving_serviceid:
            receiving_user = (self.dbsession.query(db.AuthenticatedUser)
                              .filter(AuthenticatedUser.serviceid == receiving_serviceid))
            # If the receiving user is not the frontier user, do nothing
            if room.frontier_user != receving_user:
                return

        user = self.dbsession.query(db.User).filter(db.User.serviceid == service_userid).one()

        if not user in room.users:
            raise ValueError("The user '{}' has not been added to this room.".format(service_userid))

        return await self.matrix_send_message(user, room, message)

    def add_authenticated_user(self, matrixid, serviceid, auth_token, nick=None):
        """
        Add an authenticated user to the appservice.

        This user will connect when the appservice is run.

        Parameters
        ----------

        matrixid : `str`
            The matrix id of the user to add.

        serviceid : `str`
            The username/id for the service user.

        auth_token : `str`
            The authentication token for this user.

        nick : `str`, optional
            A nickname for this user.

        Returns
        -------

        connection : `object`
            A connection object, as returned by the
            ``@appservice.service_connect`` decorator.

        """
        user = db.AuthenticatedUser(matrixid, serviceid, auth_token, nick=nick)
        self.dbsession.add(user)
        self.dbsession.commit()
        return user

    async def create_linked_room(self, auth_user, matrix_roomid, service_roomid):
        """
        Create a linked room.

        This method will create a link between a service room for a single
        authenticated user (which will become the frontier user) and a matrix room.

        This method will invite the auth_user to the matrix room.

        Will not do anything if room is already linked.

        Parameters
        ----------

        auth_user : `appservice_framework.database.AuthenticatedUser`
            The authenticated user who will become the frontier user for this
            room, and will be invited to this room and added to the room in the
            database.

        matrix_roomid : `str`
            The matrix room alias of the room to create.

        service_roomid : `str`
            The service room id this room will be linked to.

        Returns
        -------

        room : `appservice_framework.database.LinkedRoom`
            The room object that has been added to the database.
        """

        try:
            alias = matrix_roomid.split(':')[0][1:]
            log.debug(f"Creating room {alias}")
            await self.api.create_room(alias=alias,
                                       is_public=self.config.invite_only_rooms,
                                       invitees=(),
                                       query_params={'auth_token': self.api.token})

        except MatrixRequestError as e:
            content = json.loads(e.content)
            if content['error'] != "Room alias already taken":
                raise e

        # Invite the user to the room
        try:
            await self.api.invite_user(await self.get_room_id(matrix_roomid), auth_user.matrixid)
        except MatrixRequestError as e:
            content = json.loads(e.content)
            if " is already in the room." not in content['error']:
                raise e

        room = db.LinkedRoom(matrix_roomid, service_roomid)
        room.users.append(auth_user)
        room.frontier_user = auth_user
        self.dbsession.add(room)
        self.dbsession.commit()

        return room

    async def add_user_to_room(self, matrix_userid, matrix_roomid):
        """
        Add a user to a room.

        If the user is an authenticated user (i.e. a real user) then they are
        invited to the room, if it is a user managed by the AS then the user is
        joined to that room.

        Will not do anything if user is already in room.

        Parameters
        ----------

        matrix_userid : `str`
            The user id to add to the room.

        matrix_roomid : `str`
            The room to add the user to.

        """
        log.debug(f"add {matrix_userid} to {matrix_roomid}")
        user = self.dbsession.query(db.User).filter(db.User.matrixid == matrix_userid).one()
        room = self.dbsession.query(db.LinkedRoom).filter(db.LinkedRoom.matrixid == matrix_roomid).one()

        if user in room.users:
            log.debug("user already in room")
            return

        room_id = await self.get_room_id(room.matrixid)
        if isinstance(user, db.AuthenticatedUser):
            await self.api.invite_user(room_id, user.matrixid)
            # TODO: We might need to only add the user to the room after the invite is accepted.
        else:
            # Invite here is for when the invite_only_rooms flag is set.
            await self.api.invite_user(room_id, user.matrixid,
                                       query_params={'auth_token': self.api.token})
            await self.api.join_room(room.matrixid,
                                     query_params={'user_id': user.matrixid,
                                                   'auth_token': self.api.token})

        room.users.append(user)
        self.dbsession.commit()
