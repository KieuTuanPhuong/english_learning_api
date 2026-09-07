"""WebSocket URL routing (parallel to config/urls.py for HTTP)."""

from django.urls import path

from core import consumers

websocket_urlpatterns = [
    path("ws/speaking/<int:exercise_id>/", consumers.SpeakingConsumer.as_asgi()),
    path("ws/notifications/", consumers.NotificationConsumer.as_asgi()),
    path("ws/meetings/<int:meeting_id>/", consumers.MeetingSignalConsumer.as_asgi()),
]
