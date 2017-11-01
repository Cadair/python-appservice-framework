from appservice_framework import AppService
from asyncirc import irc

apps = AppService("localhost:8008",
                  "localhost",
                  "wfghWEGh3wgWHEf3478sHFWE",
                  "@irc_.*",
                  "#irc_.*",
                  "sqlite:///:memory:")


@apps.service_connect_sync
def connect_irc(apps, service_id):
    """
    Connect to irc.
    """
    print("Connecting to IRC...")
    return irc.connect("localhost", 6667, use_ssl=False).join("#test1")



apps.run()
