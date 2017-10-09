Matrix Appservice Framework
===========================

The objective of this package is to provide a framework for writing bridging
appservices for matrix. These bridges can be double puppet or single puppet or a
combination of both.

The objective of this framework is that the framework handles maintaining state
and the matrix operations and provides an easy to use API to developers to
implement the service specific components.


Implementation
--------------

This framework is written using the asyncio module and the co-routine syntax
which was introduced in Python 3.5. It uses aiohttp for making HTTP requests and
for the web server component. It uses SQLAlchemy to have a database of users and
rooms that the bridge is configured for.


Planning
--------

UX Flow
#######

Double Puppet bridge
^^^^^^^^^^^^^^^^^^^^

1. Auth with bot
2. Ask for invite to room

Single Puppet bridge
^^^^^^^^^^^^^^^^^^^^

Assume everything is public (like IRC bridge but unknown rooms on service side):

`Telegram Bot <https://t2bot.io/telegram>`_ or `Discord Bot<https://t2bot.io/discord>`_

1. Invite bot user to service room
2. Link to matrix room posted in service room.
3. Join matrix room.


Keeping rooms invite only on matrix side.

1. Invite bot to matrix room
2. Invite bot to service room
3. Some token printed in the service room
4. Issue command in the matrix room with the token.


Dual Mode - Double and Single Puppet
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

1. Auth with bot
2. Ask for invite to room
3. Auth'ed user gets admin in room
4. Non-authed matrix users are puppeted by service bot user.
