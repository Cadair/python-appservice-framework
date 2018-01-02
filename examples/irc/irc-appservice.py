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
    await conn.connect()

    conn.send("NICK", nick="matrix")
    conn.send("USER", user="matrix", realname="apps")

    # Temp join for testing
    conn.send('JOIN', channel="#test")

    # Tempt send for testing
    conn.send('PRIVMSG', target='#test', message="Hello")

    return conn


user1 = apps.add_authenticated_user("@irc:localhost", "matrix", "")

# Use a context manager to ensure clean shutdown.
with apps.run() as run_forever:
    room = apps.create_linked_room(user1, "#irc_#test:localhost", "#test")
    conn = apps.get_connection(wait_for_connect=True)

    @conn.on("PRIVMSG")
    async def recieve_message(**kwargs):
        userid = kwargs['nick']
        roomid = kwargs['target']
        message = kwargs['message']
        print(message, roomid, userid)
        await apps.create_matrix_user(userid, matrix_roomid=f"#irc_{roomid}:localhost")
        await apps.relay_service_message(userid, roomid, message, None)


    run_forever()
