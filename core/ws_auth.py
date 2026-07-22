"""ASGI middleware: authenticate the WebSocket from a ?token= JWT.

DRF auth/permission classes do not apply to Channels consumers — they see the
ASGI ``scope``, not a DRF ``request``. This mirrors JWTAuthentication +
IsActiveUser for the socket surface (docs.md §6 directives #3/#4)."""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser


@database_sync_to_async
def _user_from_token(raw_token):
    # pyrefly: ignore [missing-import]
    from rest_framework_simplejwt.authentication import JWTAuthentication
    # pyrefly: ignore [missing-import]
    from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

    from core.models import UserStatus

    auth = JWTAuthentication()
    try:
        validated = auth.get_validated_token(raw_token)
        user = auth.get_user(validated)
    except Exception:
        return AnonymousUser()
    # Mirror IsActiveUser: suspended/inactive users are rejected on the socket too.
    if getattr(user, "status", None) != UserStatus.ACTIVE:
        return AnonymousUser()
    return user


class JWTAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        query = parse_qs(scope.get("query_string", b"").decode())
        token = (query.get("token") or [None])[0]
        scope["user"] = await _user_from_token(token) if token else AnonymousUser()
        return await super().__call__(scope, receive, send)
