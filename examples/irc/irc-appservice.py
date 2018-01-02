from appservice_framework import AppService
# from asyncirc import irc
import bottom
import asyncio
loop = asyncio.get_event_loop()
loop.set_debug(True)

apps = AppService("localhost:8008",
                  "localhost",
                  "wfghWEGh3wgWHEf3478sHFWE",
                  "@irc_.*",
                  "#irc_.*",
                  "sqlite:///:memory:", loop=loop)

@apps.service_connect
async def connect_irc(apps, serviceid, auth_token):
    print("Connecting to IRC...")
    conn = bottom.Client("localhost", 6667, ssl=False, loop=loop)
    @conn.on("client_connect")
    def test(**kwargs):
        print(kwargs)
    await conn.connect()
    print(conn.protocol)
    conn.send("NICK", nick="matrix")
    conn.send("USER", user="matrix", realname="apps")
    conn.send('JOIN', channel="#test")
    conn.send('PRIVMSG', target='#test', message="Hello")
    return conn


loop.run_until_complete(apps.add_authenticated_user("@irc:localhost", "matrix", ""))
conn = apps.get_connection()

@conn.on("PRIVMSG")
async def recieve_message(**kwargs):
    userid = kwargs['nick']
    roomid = kwargs['target']
    message = kwargs['message']
    await apps.relay_service_message(userid, roomid, message, None)

# Use a context manager to ensure clean shutdown.
# We can't actually do anything in the context manager.
with apps.run():
    pass
