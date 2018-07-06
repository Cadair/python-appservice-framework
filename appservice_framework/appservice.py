import os
import json
import time
import asyncio
import logging
from collections import namedtuple
from contextlib import contextmanager
from functools import wraps, partial
from urllib.parse import quote, urlparse

import aiohttp
import aiohttp.web
import sqlalchemy as sa

from matrix_client.errors import MatrixRequestError

from . import database as db
from .matrix_api import AsyncHTTPAPI as MatrixAPI

log = logging.getLogger("appservice_framework")

handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s %(name)-12s %(levelname)-8s %(message)s')
handler.setFormatter(formatter)

log.addHandler(handler)
log.setLevel(logging.DEBUG)

__all__ = ['AppService']


config = namedtuple("config", "invite_only_rooms")

class AppService:
    """
    Run the Matrix Appservice.

    This needs to maintain state of matrix rooms and bridged users in those rooms.
    """

    def __init__(self, matrix_server, server_domain, access_token,
                 user_namespace, room_namespace, sender_localpart,
                 database_url, loop=None, invite_only_rooms=False):

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
        self.appservice_userid = "@{}:{}".format(sender_localpart,
                                                 server_domain)
        self.room_namespace = room_namespace

        self.config = config(invite_only_rooms=invite_only_rooms)

        self.dbsession = db.initialize(database_url)

        # Setup web server to listen for appservice calls
        self.app = aiohttp.web.Application(loop=self.loop, client_max_size=None)
        self._routes()

        # Setup internal matrix event dispatch
        self._matrix_event_mapping()
        self.matrix_events = {}
        self.matrix_events['receive_message'] = {}
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

    def _connection_successful(self, future, *, user):
        conn, serviceid = future.result()
        log.info("Connection successful for %s", serviceid)
        if serviceid and not user.serviceid:
            user.serviceid = serviceid
            self.dbsession.commit()

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
            if user not in self.service_connections.keys():
                log.debug("connecting user: {}".format(user.matrixid))
                future = asyncio.ensure_future(
                    self.service_events['connect'](self, user.serviceid, user.auth_token))
                future.add_done_callback(partial(self._connection_successful, user=user))
                self.service_connections[user] = future

        # TODO: This should manually start the webapp.
        # We also need to make sure the things exit properly
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
                    log.exception("Handling matrix {} event failed.".format(event['type']))
                    # return aiohttp.web.Response(status=500)

        return aiohttp.web.Response(body=b"{}")

    async def _room_alias(self, request):
        """
        Handle an Appservice room_alias call.
        """
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
    # Internal Matrix Handlers and Helpers
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

        if event['sender'] == self.appservice_userid:
            return

        log.debug("Membership Event: %s", event)
        log.error("Membership event received, handling is not yet implemented.")

    async def _matrix_message(self, event):
        user_id = event['user_id']
        sender = event['sender']
        room_id = event['room_id']

        user = self.dbsession.query(db.User).filter(db.User.matrixid == user_id).one_or_none()
        if not user:
            log.error("message received with no matching user in the database")
            return

        room = self.dbsession.query(db.Room).filter(db.Room.matrixid == room_id).one_or_none()
        if not room:
            log.error("message received with no matching room in the database")
            return

        if not isinstance(room, db.LinkedRoom):
            # Handle Bot Chat messages here.
            return

        if user not in room.users:
            log.error("message received, but user is not in the room")
            return

        if isinstance(user, db.AuthenticatedUser):
            auth_user = user
        else:
            # TODO: This needs to differentiate between matrix users that are
            # not puppeted and AS users
            return
            # auth_user = room.frontier_user

        content_type = event['content']['msgtype']
        await self.matrix_events['receive_message'][content_type](self, auth_user, room, event['content'])


    async def _invite_user(self, roomid, matrixid):
        """
        Invite to a room, but ignore errors if user is already in room.
        """
        try:
            resp = await self.api.invite_user(roomid, matrixid,
                                              query_params={'auth_token': self.api.token})
        except MatrixRequestError as e:
            content = json.loads(e.content)
            if " is already in the room." not in content['error']:
                raise e
            else:
                log.debug("User %s was already in the room.", matrixid)
                return

        return resp

    ######################################################################################
    # Matrix Event Decorators
    ######################################################################################

    def matrix_recieve_message(self, coro):
        """
        A matrix 'm.room.message' event with 'm.text' type.

        coro(appservice, auth_user, room, content)
        """
        self.matrix_events['receive_message']['m.text'] = coro

        return coro

    def matrix_recieve_image(self, coro):
        """
        A matrix 'm.room.message' event with 'm.image' type.

        coro(appservice, auth_user, room, content)
        """
        self.matrix_events['receive_message']['m.image'] = coro

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
    # TODO: Add a hook for plain text or html messages?

    ######################################################################################
    # Service Event Decorators
    ######################################################################################

    def service_connect(self, coro):
        """
        A decorator to register the connection function.

        This function is called for every
        `~appservice_framework.database.AuthenticatedUser` on ``run()``.


        **Function Signature**

        ``coro(appservice, serviceid, auth_token)``

        *Returns*

        | `service` : `object`
        |     An object representing the connection.
        | `service_userid` : `str` or `None`
        |     The service user id of the connected user
        | `service_userid` : `str` or `None`
        |     The service user id of the connected usu
        """

        self.service_events['connect'] = self._make_async(coro)

        return coro

    def service_room_exists(self, coro):
        """
        Decorator to query if a service room exists.

        ``coro(appservice, service_roomid)``

        """
        self.service_events['room_exists'] = self._make_async(coro)

        return coro

    def service_join_room(self, coro):
        """
        This function is called when an authenticated user joins a new service
        room, i.e. a room that exists but the service account is not currently
        a member of.

        ``coro(appservice, service_userid, service_roomid)``
        """

        async def join_room(self, service_userid, service_roomid, matrix_roomid=None):
            # This function is called when a user has joined a room on the
            # matrix side, so we only need to handle service and database.

            await coro(self, service_userid, service_roomid, matrix_roomid=None)

            room = await self.create_linked_room(auth_user, service_roomid, matrix_roomid=None)
            user = self.get_user(serviceid=service_userid)

            room.users.append(user)

            self.dbsession.commit()

        self.service_events['join_room'] = join_room

        return join_room

    def service_part_room(self, coro):
        """
        This is called when a matrix user leaves a room.

        ``coro(appservice, user, room)``
        """
        async def part_room(self, user, room):
            await coro(self, user, room)

            # Do database stuff
            room.users.remove(user)

            if user is room.frontier_user:
                if room.auth_users:
                    room.frontier_user = room.auth_users[0]
                else:
                    room.active = False
                    # TODO: If all the auth_users have left the room needs shutting down.

            self.dbsession.commit()

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
    # Service Event Functions
    ######################################################################################
    # These are methods the service needs to call when events happen.

    async def relay_service_message(self, service_userid, service_roomid,
                                    message, receiving_serviceid=None):
        """
        Forward a message to matrix.

        Parameters
        ----------
        service_userid : `str`
            Service User ID

        service_roomid : `str`
            Service Room ID

        message : `str` or `dict`
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
            # If the receiving user is not the frontier user, do nothing
            if room.frontier_user.serviceid != receiving_serviceid:
                log.debug("%s is not the frontier user", receiving_serviceid)
                return

        # Get all the users in the db for this service id
        user = self.dbsession.query(db.User).filter(db.User.serviceid == service_userid).all()

        if len(user) > 1:
            # If there is more than one user in the DB, get all the non-auth
            # users (ones we can send messages as)
            user = list(filter(lambda x: not isinstance(x, db.AuthenticatedUser), user))
            # If the user is an auth user we can't send messages for them
            if not user:
                return

        if len(user) > 1:
            log.debug("Multiple non-auth users matched for {}".format(service_userid))

        # Otherwise take the first user that matches and hope it's the right one
        user = user[0]

        if user not in room.users:
            raise ValueError("The user '{}' has not been added to this room.".format(service_userid))

        return await self.matrix_send_message(user, room, message)

    async def relay_service_image(self, service_userid, service_roomid,
                                  image_url, receiving_serviceid=None,
                                  filename=None):
        p = urlparse(image_url)
        if p.scheme != "mxc":
            user = self.dbsession.query(db.User).filter(db.User.serviceid == service_userid).one()
            image_url = await self.upload_image_to_matrix(user.matrixid, image_url)

        # Take the last section of the path to be the name
        if not filename:
            filename = os.path.split(p.path)[1]

        content_pack = {
            "url": image_url,
            "msgtype": "m.image",
            "body": filename,
            "info": {}
        }

        return await self.relay_service_message(service_userid, service_roomid,
                                                content_pack, receiving_serviceid)

    async def service_user_join(self, service_userid, service_roomid):
        """
        Called when a service user joins a room.

        The service user is added to the room (and created it needed),
        """

    async def service_user_part(self, service_userid, service_roomid):
        """
        Called when a service user leaves room.

        The corresponding matrix user will part the room if managed by the AS.
        """

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

    async def matrix_send_message(self, user, room, content):
        """
        Send a message to a matrix room as a matrix user.

        Parameters
        ----------

        user : `appservice_framework.database.User`
            The user to send the message as.

        room : `appservice_framework.database.Room`
            The Room to send the message to.
        content : `dict` or `str`
            The content or text to send
        """

        if isinstance(content, str):
            content = self.api.get_text_body(content, "m.text")

        mxid = user.matrixid

        return await self.api.send_message_event(room.matrixid, "m.room.message",
                                                 content,
                                                 query_params={'user_id': mxid,
                                                               'auth_token': self.api.token})

    async def create_matrix_user(self, service_userid, matrix_userid=None,
                                 nick=None, matrix_roomid=None):
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
            matrix_userid = "{prefix}{service_userid}:{server_name}".format(prefix=prefix,
                                                                            service_userid=service_userid,
                                                                            server_name=self.server_name)

        # Localpart is everything before : without #
        localpart = matrix_userid.split(':')[0][1:]

        user = db.User(matrix_userid, service_userid, nick=nick)
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

        if nick:
            await self.api.set_display_name(matrix_userid, nick,
                                            query_params={'user_id': matrix_userid,
                                                          'auth_token': self.api.token})

        return user

    async def upload_image_to_matrix(self, matrix_userid, image_url):
        """
        Given a URL upload the image to the homeserver for the given user.
        """
        async with self.http_session.request("GET", image_url) as resp:
            data = await resp.read()

        json = await self.api.media_upload(data, resp.content_type,
                                           query_params={'user_id': matrix_userid,
                                                         'auth_token': self.api.token})
        return json['content_uri']

    async def set_matrix_profile_image(self, user_id, image_url, force=False):
        """
        Set the profile image for a matrix user.
        """
        if force or not (await self.api.get_avatar_url(user_id) and image_url):
            log.debug("Setting profile picture for %s, %s", user_id, image_url)

            # Upload to homeserver
            avatar_url = await self.upload_image_to_matrix(user_id, image_url)

            # Set profile picture
            resp = await self.api.set_avatar_url(user_id, avatar_url,
                                                 query_params={'auth_token': self.api.token,
                                                               'user_id': user_id})

            return resp

    async def set_matrix_room_image(self, room_id, image_url, force=False):
        """
        Set the avatar image for a matrix room.
        """
        if force or not (await self.api.get_room_avatar(room_id) and image_url):
            log.debug("Setting room avatar picture for %s, %s", room_id, image_url)

            # Upload to homeserver
            avatar_url = await self.upload_image_to_matrix(self.appservice_userid, image_url)

            # Set profile picture
            resp = await self.api.set_room_avatar(room_id, avatar_url,
                                                      query_params={'auth_token': self.api.token,
                                                                    'user_id': self.appservice_userid}
                                                      )

            return resp

    ######################################################################################
    # Appservice Helper Methods
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
            authuser = self.dbsession.query(db.User).filter(db.User.serviceid == serviceid)
            connection = self.service_connections[authuser]

        if wait_for_connect:
            return self.loop.run_until_complete(connection)
        else:
            return connection

    def get_user(self, matrixid=None, serviceid=None, user_type='service'):
        """
        Get a `appservice_framework.database.User` object based on IDs.

        Parameters
        ----------

        matrixid : `str`, optional
            The matrix id of the user to lookup.

        serviceid : `str`, optional
            The service id of the user to lookup.

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
            filterexp = db.User.serviceid == serviceid

        return self.dbsession.query(db.User).filter(filterexp).filter(db.User.type == user_type).one_or_none()

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
            filterexp = db.Room.matrixalias == sa.text(matrixid)
            return self.dbsession.query(db.Room).filter(filterexp).one_or_none()
        if serviceid:
            return self.dbsession.query(db.LinkedRoom).filter(db.LinkedRoom.serviceid == serviceid).one_or_none()


    def add_authenticated_user(self, matrixid, auth_token, serviceid=None, nick=None):
        """
        Add an authenticated user to the appservice.

        This user will connect when the appservice is run, if serviceid was not
        specified it must be returned by the connect decorator.

        Parameters
        ----------

        matrixid : `str`
            The matrix id of the user to add.

        auth_token : `str`
            The authentication token for this user.

        serviceid : `str`, optional
            The username/id for the service user.

        nick : `str`, optional
            A nickname for this user.

        Returns
        -------

        connection : `object`
            A connection object, as returned by the
            ``@appservice.service_connect`` decorator.

        """
        user = db.AuthenticatedUser(matrixid, auth_token, serviceid=serviceid, nick=nick)
        self.dbsession.add(user)
        self.dbsession.commit()
        return user

    async def create_linked_room(self, auth_user, service_roomid, matrix_roomid=None, matrix_roomname=None):
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

        service_roomid : `str`
            The service room id this room will be linked to.

        matrix_roomid : `str`, optional
            The matrix room alias of the room to create.

        Returns
        -------

        room : `~appservice_framework.database.LinkedRoom`
            The room object that has been added to the database.

        """

        prefix = self.room_namespace.split(".*")[0]
        if not matrix_roomid:
            matrix_roomid = "{prefix}{service_roomid}:{server_name}".format(prefix=prefix,
                                                                            service_roomid=service_roomid,
                                                                            server_name=self.server_name)
        try:
            alias = matrix_roomid.split(':')[0][1:]
            log.debug("Creating room {}".format(alias))
            resp = await self.api.create_room(alias=alias,
                                              is_public=self.config.invite_only_rooms,
                                              invitees=(),
                                              query_params={'auth_token': self.api.token})

        except MatrixRequestError as e:
            content = json.loads(e.content)
            if content['error'] != "Room alias already taken":
                raise e

        roomid = await self.get_room_id(matrix_roomid)

        if matrix_roomname:
            resp = await self.api.set_room_name(roomid,
                                                matrix_roomname,
                                                query_params={'auth_token': self.api.token})

        # Invite the user to the room, but not if they are already in the room.
        await self._invite_user(roomid, auth_user.matrixid)

        room = db.LinkedRoom(matrix_roomid, roomid, service_roomid)
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
        log.debug("add {} to {}".format(matrix_userid, matrix_roomid))
        user = self.dbsession.query(db.User).filter(db.User.matrixid == matrix_userid).one()
        room = self.dbsession.query(db.LinkedRoom).filter(db.LinkedRoom.matrixalias == matrix_roomid).one()

        if user in room.users:
            log.debug("user already in room")
            return

        room_id = await self.get_room_id(room.matrixalias)
        if isinstance(user, db.AuthenticatedUser):
            await self._invite_user(room_id, user.matrixid)
            # TODO: We might need to only add the user to the room after the invite is accepted.
        else:
            # Invite here is for when the invite_only_rooms flag is set.
            await self._invite_user(room_id, user.matrixid)
            await self.api.join_room(room.matrixalias,
                                     query_params={'user_id': user.matrixid,
                                                   'auth_token': self.api.token})

        room.users.append(user)
        self.dbsession.commit()
