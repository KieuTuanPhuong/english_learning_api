"""Seed the mock-test format registry and its score-conversion tables.

    python manage.py seed_test_formats

Idempotent (update_or_create), so it is safe to re-run after editing a table
here. Everything this command writes is *data*: admins can edit the same rows
through /api/mock-tests/formats/{id}/conversions/ without a deploy.

Provenance warning — neither IELTS nor ETS publishes an official raw -> score
conversion table. The mappings below are the widely-published prep-industry
approximations, recorded in each row's `source_note`; reports label scores
"estimated" on the strength of that.
"""

from django.core.management.base import BaseCommand

from core.models import (
    OverallStrategy,
    ScoreConversionTable,
    SectionSkill,
    TestFormat,
)

IELTS_SOURCE = (
    "Approximate band conversion (prep-industry consensus, e.g. "
    "https://www.pw.live/study-abroad/ielts/exams/ielts-exam-pattern); "
    "IELTS does not publish an official raw->band table."
)
TOEIC_SOURCE = (
    "Approximate raw->scaled conversion interpolated between published "
    "anchor points (e.g. https://990prep.com/en/guides/"
    "toeic-test-format-breakdown); ETS does not publish official tables."
)
PRODUCTIVE_SOURCE = (
    "Internal mapping from the platform's 0-100 Feedback score to an IELTS "
    "band. Not an official rubric conversion."
)

# --- IELTS: raw correct out of 40 -> band. Sparse: lookup falls back to the
# nearest lower key, so only band boundaries are listed.
IELTS_LISTENING = {
    "39": "9.0", "37": "8.5", "35": "8.0", "32": "7.5", "30": "7.0",
    "26": "6.5", "23": "6.0", "18": "5.5", "16": "5.0", "13": "4.5",
    "10": "4.0", "8": "3.5", "6": "3.0", "4": "2.5", "0": "0.0",
}
IELTS_READING = {  # Academic Reading is a harsher curve than Listening
    "39": "9.0", "37": "8.5", "35": "8.0", "33": "7.5", "30": "7.0",
    "27": "6.5", "23": "6.0", "19": "5.5", "15": "5.0", "13": "4.5",
    "10": "4.0", "8": "3.5", "6": "3.0", "4": "2.5", "0": "0.0",
}

# TOEIC L&R: raw correct out of 100 per section -> scaled 5-495. Published
# anchor points; everything between them is linearly interpolated below.
TOEIC_LISTENING_ANCHORS = {
    0: 5, 10: 55, 20: 115, 30: 175, 40: 235, 50: 290,
    60: 340, 70: 390, 80: 430, 90: 465, 96: 495, 100: 495,
}
TOEIC_READING_ANCHORS = {
    0: 5, 10: 35, 20: 80, 30: 130, 40: 185, 50: 240,
    60: 300, 70: 355, 80: 405, 90: 450, 96: 495, 100: 495,
}


def _interpolate(anchors: dict) -> dict:
    """Fill every raw value between the anchor points with a straight line, so
    the stored table is complete data rather than a formula hidden in code."""
    points = sorted(anchors.items())
    table = {}
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        span = x1 - x0
        for raw in range(x0, x1):
            table[str(raw)] = str(round(y0 + (y1 - y0) * (raw - x0) / span))
    last_x, last_y = points[-1]
    table[str(last_x)] = str(last_y)
    return table


def _productive_band_table() -> dict:
    """Feedback score (0-100) -> IELTS band, in half-band steps."""
    return {
        str(score): f"{round(score / 100 * 9 * 2) / 2:.1f}"
        for score in range(0, 101, 5)
    }


CUSTOM_SOURCE = (
    "Identity mapping: a custom test scores as the percentage it actually is. "
    "No exam band is claimed or implied."
)


def _percent_table() -> dict:
    """Identity 0-100. ``convert_score`` rescales a raw count onto the table's
    domain before lookup, so a 17-question custom section lands on its true
    percentage without the format needing to know its length."""
    return {str(value): str(value) for value in range(0, 101)}


FORMATS = [
    {
        "slug": "ielts_academic",
        "name": "IELTS Academic",
        "overall_strategy": OverallStrategy.BAND_AVERAGE,
        "score_precision": "0.5",
        "is_active": True,
        "conversions": [
            (SectionSkill.LISTENING, IELTS_LISTENING, IELTS_SOURCE),
            (SectionSkill.READING, IELTS_READING, IELTS_SOURCE),
            (SectionSkill.WRITING, _productive_band_table(), PRODUCTIVE_SOURCE),
            (SectionSkill.SPEAKING, _productive_band_table(), PRODUCTIVE_SOURCE),
        ],
    },
    {
        # In-house tests: placement checks, unit reviews, anything that is not a
        # named public exam. Percentage in, percentage out — borrowing the IELTS
        # band scale for a test never calibrated against it would produce a
        # number that looks official and means nothing.
        "slug": "custom",
        "name": "Custom test",
        "overall_strategy": OverallStrategy.MEAN_PERCENT,
        "score_precision": "1",
        "is_active": True,
        "conversions": [
            (skill, _percent_table(), CUSTOM_SOURCE) for skill in SectionSkill
        ],
    },
    {
        # Retired: the library focuses on IELTS and custom tests. The row and its
        # conversion tables stay so any TOEIC attempt already sat still converts,
        # and templates PROTECT it from deletion — `is_active=False` is what
        # takes it off the shelf.
        "slug": "toeic_lr",
        "name": "TOEIC Listening & Reading",
        "overall_strategy": OverallStrategy.SCALED_SUM,
        "score_precision": "5",
        "is_active": False,
        "conversions": [
            (SectionSkill.LISTENING, _interpolate(TOEIC_LISTENING_ANCHORS), TOEIC_SOURCE),
            (SectionSkill.READING, _interpolate(TOEIC_READING_ANCHORS), TOEIC_SOURCE),
        ],
    },
]


class Command(BaseCommand):
    help = "Seed TestFormat rows and their score-conversion tables (idempotent)."

    def add_arguments(self, parser):
        # Not `--version`: BaseCommand already registers that globally, and
        # argparse raises a conflict while loading the command.
        parser.add_argument(
            "--table-version", default="2026",
            help="Version stamp pinning these conversion tables (default: 2026).",
        )

    def handle(self, *args, **options):
        version = options["table_version"]
        for spec in FORMATS:
            fmt, created = TestFormat.objects.update_or_create(
                slug=spec["slug"],
                defaults={
                    "name": spec["name"],
                    "version": version,
                    "overall_strategy": spec["overall_strategy"],
                    "score_precision": spec["score_precision"],
                    "is_active": spec["is_active"],
                },
            )
            for skill, mapping, note in spec["conversions"]:
                ScoreConversionTable.objects.update_or_create(
                    format=fmt, skill=skill,
                    defaults={"mapping": mapping, "source_note": note},
                )
            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if created else 'Updated'} {fmt.slug} "
                f"({len(spec['conversions'])} conversion tables)"
            ))
