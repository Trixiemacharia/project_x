from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path("ws/transactions/", consumers.CustomerConsumer.as_asgi()),
    path("ws/fraud/", consumers.AnalystConsumer.as_asgi()),
]