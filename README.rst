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
