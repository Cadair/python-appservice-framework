import asyncio
from functools import wraps, partial

import aiohttp
import aiohttp.web

from . import database
from .async_matrix_api import AsyncHTTPAPI


class AppService:
    """
    Run the Matrix Appservice.

    This needs to maintain state of matrix rooms and bridged users in those rooms.
    """

    def __init__(self, matrix_server, server_domain, access_token,
                 user_namespace, room_namespace, database_url, loop=None):

        if loop:
            self.loop = loop
        else:
            self.loop = asyncio.get_event_loop()

        self.client_session = aiohttp.ClientSession(loop=self.loop)
        self.api = AsyncHTTPAPI(matrix_server, self.client_session, access_token)

        self.access_token = access_token
        self.server_name = server_domain
        self.user_namespace = user_namespace
        self.room_namespace = room_namespace

        self.database_session = database.initialize(database_url)

        # Setup web server to listen for appservice calls
        self.app = aiohttp.web.Application(loop=self.loop)
        self._routes()

        # Setup internal matrix event dispatch
        self._matrix_event_mapping()
        self.matrix_events = {}
        self.service_events = {}

    def _make_async(self, call):

        if not asyncio.iscoroutinefunction(call):
            @wraps(call)
            def caller(*args, **kwargs):
                self.loop.run_soon(partial(call, **kwargs), args)

            return caller

        else:
            return call

    ######################################################################################
    # Appservice Web Server Handles
    ######################################################################################

    def run(self, host="127.0.0.1", port=5000):
        """
        Run the appservice.
        """
        service_connect = self.service_events.get('connect', None)
        service_connect_sync = self.service_events.get('connect_sync', None)

        if service_connect:
            self.loop.run_until_complete(service_connect())

        elif service_connect_sync:
            service_connect_sync()

        else:
            raise KeyError("You must define a @appservice.service_connect"
                           "coroutine or a @appservice.connect_sync function.")

        aiohttp.web.run_app(self.app, host=host, port=port)

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
        print(event)

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

        async def connect_service():
            self.service = await coro(self)

        self.service_events['connect'] = connect_service

        return connect_service

    def service_connect_sync(self, func):
        """
        Connect to the service synchronously.

        func(appservice, serviceid, auth_token)

        Returns:
            `service` an object representing the connection, becomes ``appservice.service``.
        """
        if 'connect' in self.service_events.keys():
            raise ValueError("Please define either connect or connect_sync not both.")

        def connect_service():
            self.service = func(self)

        self.service_events['connect_sync'] = connect_service

        return connect_service

    def service_recieve_message(self, coro):
        """
        Decorator for when an authenticated user receives a message.

        coro(appservice)

        Returns:
            service_userid : `str`
            service_roomid : `str`
            message_plain : `str`
        """
        # TODO: Handle plain/HTML/markdown
        # TODO: Frontier user filtering

        self.service_events['recieve_message'] = coro

        return coro

    def service_room_exists(self, coro):
        """
        Decorator to query if a matrix room alias exists.

        coro(appservice, matrix_room_alias)
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
    # Public Appservice Methods
    ######################################################################################

    async def add_authenticated_user(self, mxid, service_id, username, token):
        """
        Login an authenticated user with the appservice.
        """

    async def create_linked_room(self, mxid, matrix_roomid, service_roomid, auth_mxid):
        """
        Create a linked room.

        This method will create a link between a service room for a single
        authenticated user (others can be added afterwards) and a matrix room.

        Will not do anything if room is already linked.
        """

    async def linked_room_exists(self, matrix_roomid=None, service_roomid=None):
        """
        Check to see if a room is already linked.

        Takes *either* a matrix or service room id.
        """

    async def add_auth_user_to_room(self, auth_mxid, matrix_roomid):
        """
        Add an authenticated user to a room.

        Will not do anything if user is already in room.
        """

    async def set_matrix_profile_image(self, user_id, image_url, force=False):
        """
        Set the profile image for a matrix user.
        """
        if force or not await self.matrix_client.get_avatar_url(user_id) and image_url:
            # Download profile picture
            async with self.client_session.request("GET", image_url) as resp:
                data = await resp.read()

            # Upload to homeserver
            resp = await self.matrix_client.media_upload(data, resp.content_type,
                                                         user_id=user_id)
            json = await resp.json()
            avatar_url = json['content_uri']

            # Set profile picture
            resp = await self.matrix_client.set_avatar_url(user_id, avatar_url)

            return resp
