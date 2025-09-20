from django.urls import re_path, path
from . import consumers

websocket_urlpatterns = [
    re_path(r'^ws/room/speed/(?P<room_name>\w+)/$', consumers.GameConsumer.as_asgi()),
    re_path(r'^ws/room/guess/(?P<room_name>\w+)/$', consumers.GuessCountGameConsumer.as_asgi()),
    re_path(r'ws/lobby/(?P<mode>speed|guess)/$', consumers.LobbyConsumer.as_asgi()),
    # re_path(r'ws/room/guess_count/(?P<room_name>\w+)/$', consumers.GuessCountGameConsumer.as_asgi())
]