"""Signed, expiring URLs for uploaded media, and the view that serves them.

Why this exists
---------------
Uploaded audio is student voice: pronunciation attempts and mock-test Speaking
answers. Django only serves ``MEDIA_URL`` itself while ``DEBUG`` is on, so a
production deploy has two options — 404 on every recording, or an Nginx
``alias`` that makes every file world-readable to anyone holding the URL. The
second is what the deploy guide used to recommend, and mock-test uploads keep
the browser's filename (``answer.webm``), so those URLs are guessable.

Instead, a stored file is handed out as ``/media/<path>?t=<signed token>``. The
token carries the path, is signed with ``SECRET_KEY`` and expires after
``settings.MEDIA_URL_TTL``. That works in a plain ``<audio src>`` tag — which
cannot send an ``Authorization`` header — while a leaked link stops working.
It is the same shape as the S3 presigned URLs this is meant to become.

``ProtectedMediaView`` accepts a valid token, or a logged-in session user (so
links in the Django admin stay clickable), and nothing else.
"""

from __future__ import annotations

import posixpath
from pathlib import Path
from urllib.parse import urlencode, urlparse

from django.conf import settings
from django.core import signing
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden
from django.views import View

# Namespaces the signature: a token minted here cannot be replayed against any
# other `signing.dumps` user in the project.
SALT = "core.media.v1"
TOKEN_PARAM = "t"


class InvalidMediaToken(Exception):
    """Token missing, tampered with, or past ``MEDIA_URL_TTL``."""


def relative_path(url_or_name) -> str:
    """Reduce anything that identifies a stored file to a MEDIA_ROOT-relative path.

    Accepts what the codebase actually stores: a ``FileField.name``
    (``pronunciation/2026/09/a.webm``), a ``MEDIA_URL`` path
    (``/media/mock-tests/2026/09/answer.webm``), an absolute URL pointing at
    either, and any of those already carrying a ``?t=`` token.
    """
    if not url_or_name:
        return ""
    # urlparse handles both absolute URLs and bare paths, and drops the query
    # string so an already-signed URL can be re-signed.
    parsed = urlparse(str(url_or_name))
    path = parsed.path
    media_url = (getattr(settings, "MEDIA_URL", "/media/") or "/media/")
    if path.startswith(media_url):
        return path[len(media_url):].lstrip("/")
    if parsed.scheme or parsed.netloc:
        # Absolute URL somewhere else entirely (a CDN, or the seed data's
        # mock.cdn links). Not ours to sign — callers fall back to it as-is.
        return ""
    return path.lstrip("/")


def sign(url_or_name) -> str:
    """Mint a token for a stored file. Returns '' when there is no file."""
    name = relative_path(url_or_name)
    if not name:
        return ""
    return signing.dumps(name, salt=SALT, compress=True)


def unsign(token: str, max_age: int | None = None) -> str:
    """Path carried by ``token``, or raise :class:`InvalidMediaToken`."""
    if not token:
        raise InvalidMediaToken("no token")
    if max_age is None:
        max_age = getattr(settings, "MEDIA_URL_TTL", 7 * 24 * 3600)
    try:
        name = signing.loads(token, salt=SALT, max_age=max_age)
    except signing.SignatureExpired as exc:
        raise InvalidMediaToken("token expired") from exc
    except signing.BadSignature as exc:
        raise InvalidMediaToken("bad token") from exc
    if not isinstance(name, str):
        raise InvalidMediaToken("bad payload")
    return name


def signed_url(url_or_name, request=None) -> str:
    """``/media/<path>?t=<token>`` for a stored file, absolute if ``request``
    is given. Returns '' when there is no file, so serializers can pass a
    missing FileField straight through."""
    name = relative_path(url_or_name)
    if not name:
        return ""
    media_url = (getattr(settings, "MEDIA_URL", "/media/") or "/media/")
    url = f"{media_url}{name}?{urlencode({TOKEN_PARAM: sign(name)})}"
    return request.build_absolute_uri(url) if request is not None else url


def resolve_on_disk(name: str) -> Path:
    """MEDIA_ROOT-relative path -> absolute path, refusing anything that escapes
    MEDIA_ROOT (``../``, absolute paths, symlink games)."""
    media_root = Path(settings.MEDIA_ROOT).resolve()
    # normpath collapses ".." *before* touching the filesystem, so traversal is
    # rejected even when the target does not exist.
    clean = posixpath.normpath("/" + str(name).replace("\\", "/")).lstrip("/")
    candidate = (media_root / clean).resolve()
    if candidate != media_root and media_root not in candidate.parents:
        raise Http404("media path outside MEDIA_ROOT")
    return candidate


class ProtectedMediaView(View):
    """Serve one file from ``MEDIA_ROOT`` to a caller that proves it may.

    A plain Django view, not DRF: the caller is usually an ``<audio>`` element,
    which sends cookies but never an ``Authorization`` header. Access needs
    either a valid signed token (the normal path, minted by :func:`signed_url`)
    or an authenticated session (an admin clicking through Django admin).
    """

    def get(self, request, path: str):
        token = request.GET.get(TOKEN_PARAM, "")
        name = ""
        if token:
            try:
                name = unsign(token)
            except InvalidMediaToken as exc:
                return HttpResponseForbidden(f"Media link is not valid ({exc}).")
            # The token is authoritative; the visible path must agree with it,
            # so one valid token cannot be reused to fetch a different file.
            if relative_path(f"/{path}") != name:
                return HttpResponseForbidden("Media link does not match this file.")
        elif request.user.is_authenticated:
            name = relative_path(f"/{path}")
        else:
            return HttpResponseForbidden(
                "Media requires a signed link or an authenticated session."
            )

        full_path = resolve_on_disk(name)
        if not full_path.is_file():
            raise Http404("media file not found")

        if getattr(settings, "MEDIA_X_ACCEL_REDIRECT", False):
            # Nginx streams the bytes; this worker is released immediately.
            response = HttpResponse(status=200)
            prefix = settings.MEDIA_X_ACCEL_PREFIX.rstrip("/")
            response["X-Accel-Redirect"] = f"{prefix}/{name}"
            del response["Content-Type"]  # let Nginx set it
            return response

        # Cacheable by the browser but never by a shared proxy: the URL is a
        # capability, and the response is one student's recording.
        response = FileResponse(full_path.open("rb"))
        response["Cache-Control"] = "private, max-age=3600"
        return response
