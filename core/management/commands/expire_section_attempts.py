"""Force-finalize mock-test sections whose clock ran out and were never
submitted, so an abandoned attempt still produces a score report.

    python manage.py expire_section_attempts

Run it on a cron (every few minutes is plenty). It is the documented Celery-beat
boundary: when a broker lands, schedule `mock_tests.expire_stale_sections`
directly and keep this command as the manual escape hatch.

A section is only swept once it has been expired for `mock_tests.SWEEP_AFTER`
(10 minutes), so a student who reconnects just after time-up can still press
Submit themselves. Grading uses the last draft the server accepted either way.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from core import mock_tests
from core.models import SectionAttempt, SectionStatus


class Command(BaseCommand):
    help = "Finalize expired, unsubmitted mock-test sections."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report how many sections would be finalized, and change nothing.",
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            cutoff = timezone.now() - mock_tests.SWEEP_AFTER
            count = SectionAttempt.objects.filter(
                status=SectionStatus.IN_PROGRESS, expires_at__lt=cutoff
            ).count()
            self.stdout.write(f"{count} section(s) would be finalized")
            return

        count = mock_tests.expire_stale_sections()
        self.stdout.write(self.style.SUCCESS(f"Finalized {count} section(s)"))
