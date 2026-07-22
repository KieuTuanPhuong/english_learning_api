"""AI evaluation suite (docs.md §1.5/§3.4).

Mockable by design: ``AI_BACKEND=mock`` (default) uses a deterministic stub so
the API shape matches docs.md without live OpenAI/Whisper keys. ``AI_BACKEND=real``
selects the documented (stubbed) HTTP backend. See ``backends.py`` for the
stub-vs-real boundary and the env/keys a production deploy needs.
"""

from .service import evaluate_submission, get_backend, grade_submission  # noqa: F401
