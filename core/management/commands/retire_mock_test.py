"""Remove a mock test from the library.

    python manage.py retire_mock_test --title "IELTS Academic — Practice Test 1"
    python manage.py retire_mock_test --id 1 --delete
    python manage.py retire_mock_test --id 1 --delete --with-attempts

Reports by default and deletes only when asked, because the interesting question
is never "does this row exist" but "what else goes with it".

``TestAttempt.template`` and ``SectionAttempt.section`` are PROTECTed on purpose:
deleting a test that students have sat destroys their score reports, and that
must be a decision someone makes out loud rather than a cascade they discover
afterwards. So a template with attempts refuses to delete unless
``--with-attempts`` is passed, and the report names the students first.

Exercises are never deleted. They are independently owned content — a teacher may
use a passage outside any test, and other tests may share it — so retiring a test
unlinks them and leaves them in the library.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import MockTestTemplate, Submission, TestAttempt


class Command(BaseCommand):
    help = "Report on, and optionally delete, a mock-test template."

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--id", type=int, help="Template id.")
        target.add_argument("--title", help="Exact template title.")
        parser.add_argument(
            "--delete", action="store_true",
            help="Actually delete. Without this the command only reports.",
        )
        parser.add_argument(
            "--with-attempts", action="store_true",
            help=(
                "Also delete the attempts and submissions that PROTECT this "
                "template. This destroys those students' score reports."
            ),
        )

    def handle(self, *args, **options):
        template = self._resolve(options)
        attempts = TestAttempt.objects.filter(template=template).select_related(
            "student"
        )
        submissions = Submission.objects.filter(
            section_link__section_attempt__attempt__in=attempts
        )

        self.stdout.write(f"Template {template.id}: {template.title!r}")
        self.stdout.write(f"  format   : {template.format.slug}")
        self.stdout.write(f"  sections : {template.sections.count()}")
        self._report_exercises(template)
        self._report_attempts(attempts, submissions)

        if not options["delete"]:
            self.stdout.write(self.style.WARNING(
                "\nDry run. Re-run with --delete to remove it."
            ))
            return

        if attempts.exists() and not options["with_attempts"]:
            raise CommandError(
                f"{attempts.count()} attempt(s) exist. Deleting the template "
                "would destroy those students' score reports. Re-run with "
                "--with-attempts if that is what you intend."
            )

        # Read the identity before deleting: Django clears the pk on the
        # in-memory instance, so reporting it afterwards prints "None".
        deleted_id, deleted_title = template.id, template.title

        with transaction.atomic():
            if attempts.exists():
                # Order matters: the submissions are reached *through* the
                # attempts, so collect their ids before those rows disappear.
                submission_ids = list(submissions.values_list("id", flat=True))
                attempts.delete()
                Submission.objects.filter(id__in=submission_ids).delete()
                self.stdout.write(
                    f"\nRemoved {len(submission_ids)} submission(s) and their attempts."
                )
            template.delete()

        self.stdout.write(self.style.SUCCESS(
            f"Deleted template {deleted_id}: {deleted_title!r}"
        ))

    def _resolve(self, options):
        if options["id"] is not None:
            template = MockTestTemplate.objects.filter(id=options["id"]).first()
            label = f"id={options['id']}"
        else:
            template = MockTestTemplate.objects.filter(title=options["title"]).first()
            label = f"title={options['title']!r}"
        if template is None:
            raise CommandError(f"No mock test found for {label}.")
        return template

    def _report_exercises(self, template):
        """Exercises survive the delete; say which ones and whether anything else
        still uses them, so nobody has to work it out afterwards."""
        self.stdout.write("  exercises (kept, only unlinked):")
        for section in template.sections.all():
            for item in section.items.select_related("exercise"):
                shared = item.exercise.test_sections.exclude(
                    section__template=template
                ).count()
                note = (
                    f"also used by {shared} other test section(s)" if shared
                    else "becomes unused"
                )
                self.stdout.write(
                    f"    {item.exercise_id:>4} {item.exercise.title[:52]:52} {note}"
                )

    def _report_attempts(self, attempts, submissions):
        count = attempts.count()
        if not count:
            self.stdout.write("  attempts : none — safe to delete")
            return
        self.stdout.write(self.style.WARNING(
            f"  attempts : {count} — deleting loses these score reports"
        ))
        for attempt in attempts:
            self.stdout.write(
                f"    attempt {attempt.id}: {attempt.student.email} "
                f"status={attempt.status} overall={attempt.overall_score} "
                f"started={attempt.started_at:%Y-%m-%d}"
            )
        self.stdout.write(f"  submissions attached: {submissions.count()}")
