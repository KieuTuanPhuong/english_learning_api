"""QA scenarios for manual local testing — additive and idempotent.

    python manage.py seed_qa                     # everything
    python manage.py seed_qa --skip-audio        # no text-to-speech (needs macOS `say`)
    python manage.py seed_qa --reset             # drop this command's rows first, then recreate
    python manage.py seed_qa --api-url http://localhost:8000

Builds on the demo dataset (``seed_demo``) and the mock-test library
(``seed_test_formats`` + ``seed_mock_tests`` + ``seed_exercise_catalog``);
each is run first when its rows are missing. Everything created here is keyed
by a ``QA ·`` title or by (student, template), so a re-run refreshes instead
of duplicating, and rows this command did not create are never touched.
Refuses to run with DEBUG off unless ``--force``.

Scenarios (password for everyone: password123):

* Meetings — three rooms for "English 101 — Beginners" (teacher emma, student
  alice): one live now, one booked for tomorrow, one already ended.
* Teacher inbox — a QA module with a reading check and a writing task assigned
  to English 101. bob's reading answers (two wrong) and alice's essay wait in
  emma's inbox; bob's essay is already graded, so bob's student view shows
  teacher feedback and the "Explain my mistakes" card.
* Listening audio — every Listening part with a transcript gets a spoken
  recording of it (macOS ``say``) under MEDIA_ROOT/qa/, attached as a signed
  media link (valid for MEDIA_URL_TTL, 7 days by default; re-run to refresh).
* Mock-test attempts, one student per scenario:

    bob      Placement check   completed, marked by the mock AI engines (instant, deterministic)
    charlie  Placement check   completed, AI marking pending -> opening the report starts the live provider
    ethan    Placement check   Reading marking failed (simulated) -> Retry; Writing marked
    fiona    IELTS full test   in progress: Listening + Reading done, Writing/Speaking not started
    george   IELTS full test   completed, all four skills marked by the mock engines
                               (spoken answers synthesised with `say`, so live re-marking has real speech)
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test.utils import override_settings
from django.utils import timezone

from core import media, mock_tests
from core.models import (
    RECEPTIVE_SKILLS,
    AiGradingStatus,
    Assignment,
    AttemptMode,
    Class,
    ClassStudent,
    Exercise,
    ExerciseType,
    Feedback,
    LearningModule,
    Meeting,
    MeetingStatus,
    MockTestTemplate,
    Question,
    QuestionOption,
    SectionSkill,
    Submission,
    SubmissionStatus,
    SubmissionType,
    TestAttempt,
    TestFormat,
    User,
    blank_answer_key,
)

QA = "QA · "
CLASS_NAME = "English 101 — Beginners"
TEACHER = "emma.teacher@english.app"
PLACEMENT = "Placement check — Reading and Writing"
IELTS = "IELTS Academic — Full Practice Test 1"
MODULE_TITLE = f"{QA}Local scenarios"
READING_TITLE = f"{QA}Reading check — why cities stay warm"
WRITING_TITLE = f"{QA}Writing — describe your hometown"
AUDIO_DIR = "qa"  # MEDIA_ROOT/qa/{listening,speaking}
VOICE = "Samantha"
SPEECH_RATE = "175"

# Transcript -> speech: drop the "PART 1 — TRANSCRIPT" heading, speaker labels
# ("ASSISTANT:", "M:") and stage directions so the voice reads only the lines.
_HEADING_RE = re.compile(r"^\s*PART\s+\d+\s*[—-].*$", re.MULTILINE)
_SPEAKER_RE = re.compile(r"^\s*[A-Z][A-Za-z' .-]{0,24}:\s*", re.MULTILINE)
_STAGE_RE = re.compile(r"[\[(][^\])]{0,40}[\])]")

READING_PASSAGE = """\
Cities are often several degrees warmer than the countryside around them, and
the gap is widest at night. During the day, dark roofs, asphalt roads and
concrete walls soak up the sun's energy. After sunset the open fields cool
quickly, but the city's stone and tarmac keep releasing the heat they stored,
so the air above them stays warm for hours. Traffic and air conditioning add a
little heat too, but the materials matter far more than the vehicles. Trees
and water work the other way: leaves spend the sun's energy evaporating water
instead of warming up, and a park can be noticeably cooler than the streets
around it. That is why cities that want cooler summers plant more trees and
paint roofs in pale colours that reflect sunlight instead of storing it.
"""

READING_QUESTIONS = [
    ("Why are cities warmer than the countryside at night?", [
        ("Because there is more traffic at night", False),
        ("Because buildings and roads release the heat they stored during the day", True),
        ("Because more people live there", False),
    ]),
    ("Which surfaces warm a city the most?", [
        ("Rivers and lakes", False),
        ("Trees and parks", False),
        ("Dark roofs and asphalt roads", True),
    ]),
    ("What does the passage suggest cities do to stay cooler?", [
        ("Plant trees and use pale, reflective roofs", True),
        ("Build taller buildings", False),
        ("Ban air conditioning", False),
    ]),
]
# bob's answers by option index: Q1 wrong, Q2 right, Q3 wrong -> 1 of 3.
READING_CHOICES = [0, 2, 1]

ALICE_ESSAY = """\
My hometown is Hai Phong, a port city in the north of Vietnam. It is not so
big like Hanoi but it have many interesting places. The most famous is the
Cat Ba island, where the tourists come in summer for swimming and the sea
food. In the city center there is a old opera house which was built by the
French, and near it a big market that open very early in the morning.

I like my hometown because the people are friendly and the food is cheap
and delicious, especially the banh da cua, a noodle soup with crab. However
the traffic become worse every year and in the rainy season the streets are
flooding. If I could change one thing, I would build more parks for the
childrens to play.
"""

BOB_ESSAY = """\
I was born in Da Lat, a small city in the highlands of Vietnam. It is famous
for its cool weather, pine forests and flowers, so many people call it the
city of eternal spring. The center has a lake, an old railway station and a
night market where you can buy strawberries and hot soy milk.

What I love most about Da Lat is the calm atmosphere. There is less noise
than in Ho Chi Minh City and you can ride a motorbike through the hills in
the morning fog. On the other hand, there are not many jobs for young people,
which is why I moved away to study. In the future I hope to come back and
open a small coffee farm with my family.
"""

BOB_FEEDBACK = (
    "A clear, well-organised description with a good opening and a personal "
    "ending. Vocabulary is varied (\"eternal spring\", \"morning fog\"). Watch "
    "sentence length in the second paragraph — one idea per sentence — and add "
    "one more specific example of what visitors can do. Next time, try a "
    "comparison paragraph (then vs now) to show more grammar range."
)

PLACEMENT_WRITING = """\
Nowadays many companies let their employees to work from home some days of
the week. In my opinion this is a good development, but it also have some
problems.

The first advantage is that people save a lot of time, because they do not
need to travel to the office every day. In big cities like Hanoi the traffic
is terrible, so this time can be used for the family or for exercise. Second,
workers can concentrate more easy, because there is no noise from colleagues.

However, working from home can make people feel lonely, and some employees
find difficult to stop working in the evening. Also, young workers learn a
lot from watching their senior colleagues, which is not possible on a video
call.

To conclude, I believe companies should allow working from home for two or
three days per week, but keep some days in the office so that the team can
still meet face to face.
"""

IELTS_TASK1 = """\
The chart shows the percentage of household waste that was recycled in four
countries between 2010 and 2020.

Overall, recycling rates increased in every country over the period, although
the size of the increase was very different. Germany recycled the most waste
in both years, rising from about 45 percent in 2010 to nearly 65 percent in
2020. The United Kingdom started much lower, at around 25 percent, but grew
steadily to reach 45 percent by the end of the period.

In contrast, the rates in Vietnam and Brazil remained low. Vietnam rose from
only 5 percent to 15 percent, while Brazil showed the smallest change, from
8 percent to about 12 percent. It is noticeable that the gap between the
highest and lowest country actually widened during these ten years.
"""

IELTS_TASK2 = """\
Since the pandemic, working from home has become normal for millions of office
workers, and some people believe it should be the standard arrangement. I
partly agree with this view, although I think a mixed model is more realistic.

There are strong arguments for remote work. Employees save hours of commuting
each week, which reduces stress and pollution, and many report that they can
concentrate better without the interruptions of an open-plan office. Companies
also benefit, because they need less office space and can hire talented people
who live far away from the city.

Nevertheless, working from home is not suitable for everyone. New employees
learn a great deal by watching experienced colleagues, and creative work often
depends on informal conversations that rarely happen on video calls. In
addition, some people do not have a quiet place to work at home, and the
border between work and private life can disappear.

In conclusion, while remote work brings real advantages, I believe the best
solution is a hybrid schedule in which staff work from home for part of the
week and meet in the office on the other days. This keeps the flexibility
that employees value while protecting teamwork and training.
"""

SPEAKING_SCRIPTS = {
    "Part 1": (
        "My name is George and I come from Da Nang, a city in the middle of "
        "Vietnam. I am studying computer science at university, it is my second "
        "year. In my free time I like play football with my friends, and "
        "sometimes I go to the beach when the weather is good. I don't really "
        "like cooking, because I am not good at it, so I usually eat at the small "
        "restaurants near my house."
    ),
    "Part 2": (
        "I would like to talk about a journey I made last summer with two "
        "friends. We went to Ha Giang by motorbike, which is in the north of "
        "Vietnam, near the border with China. The roads are very high and curvy, "
        "and sometimes I was little bit afraid, but the views was amazing, with "
        "mountains and rice fields everywhere. We stayed in a homestay with a "
        "local family, and they cooked traditional food for us. I remember this "
        "journey because it was the first time I travelled so far without my "
        "parents, and I felt very free and, how to say, independent. If I have a "
        "chance, I want to go there again in the autumn, when the flowers are "
        "blooming."
    ),
    "Part 3": (
        "I think tourism has both good and bad effects on a place like Ha Giang. "
        "On one hand, it brings money and jobs to the local people, who are quite "
        "poor. On the other hand, too many tourists can damage the environment, "
        "for example rubbish on the mountain roads, and the culture can become a "
        "kind of show for visitors. In my opinion the government should limit the "
        "number of visitors in the high season and invest in the roads and the "
        "waste collection. Also, tourists themselves should learn about the local "
        "customs before they come."
    ),
}


def _student(name: str) -> User:
    return User.objects.get(email=f"{name}.student@english.app")


def _speech_text(transcript: str) -> str:
    text = _HEADING_RE.sub("", transcript or "")
    text = _SPEAKER_RE.sub("", text)
    text = _STAGE_RE.sub("", text)
    return re.sub(r"[ \t]+", " ", text).strip()


class Command(BaseCommand):
    help = "Seed QA scenarios for manual local testing (additive, idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="Delete this command's rows and audio first, then recreate.")
        parser.add_argument("--skip-audio", action="store_true",
                            help="Do not synthesise listening/speaking audio (no macOS `say`).")
        parser.add_argument("--api-url", default="http://127.0.0.1:8000",
                            help="Public base URL of this API, used for signed audio links.")
        parser.add_argument("--force", action="store_true",
                            help="Run even though DEBUG is off.")

    # ------------------------------------------------------------------ main
    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError("seed_qa writes fake submissions under demo accounts; "
                               "refusing with DEBUG=False (pass --force to override).")
        self.api_url = options["api_url"].rstrip("/")
        self.say = None if options["skip_audio"] else shutil.which("say")
        if not options["skip_audio"] and not self.say:
            self.stdout.write(self.style.WARNING(
                "macOS `say` not found — skipping audio (listening parts keep their "
                "transcripts; speaking answers score 0)."))
        self.media_root = Path(settings.MEDIA_ROOT)

        self._ensure_base_data()
        if options["reset"]:
            self._reset()

        teacher = User.objects.get(email=TEACHER)
        klass = Class.objects.filter(class_name=CLASS_NAME, teacher=teacher).first()
        if klass is None:
            raise CommandError(f"Class '{CLASS_NAME}' for {TEACHER} not found — run seed_demo.")
        students = {name: _student(name) for name in
                    ("alice", "bob", "charlie", "ethan", "fiona", "george")}
        for student in students.values():
            ClassStudent.objects.get_or_create(klass=klass, student=student)

        with transaction.atomic():
            self._seed_meetings(klass, teacher)
            self._seed_inbox(klass, teacher, students["alice"], students["bob"])
            self._seed_listening_audio()
            self.attempts = self._seed_attempts(students)

        self._print_guide(students, klass)

    # ------------------------------------------------------------ prerequisites
    def _ensure_base_data(self):
        if not User.objects.filter(email=TEACHER).exists():
            self.stdout.write("No demo data — running seed_demo…")
            call_command("seed_demo")
        if not TestFormat.objects.filter(slug="ielts_academic").exists():
            call_command("seed_test_formats")
        if not MockTestTemplate.objects.filter(title__in=[PLACEMENT, IELTS]).count() == 2:
            call_command("seed_mock_tests")
        if not Exercise.objects.filter(band__isnull=False).exclude(band="").exists():
            call_command("seed_exercise_catalog")

    def _reset(self):
        self.stdout.write("Removing previous QA rows…")
        Meeting.objects.filter(title__startswith=QA).delete()
        module = LearningModule.objects.filter(title=MODULE_TITLE).first()
        if module is not None:
            exercises = Exercise.objects.filter(module=module)
            Submission.objects.filter(exercise__in=exercises).delete()
            Assignment.objects.filter(exercise__in=exercises).delete()
            exercises.delete()
            module.delete()
        attempts = TestAttempt.objects.filter(
            student__email__in=[f"{n}.student@english.app"
                                for n in ("bob", "charlie", "ethan", "fiona", "george")],
            template__title__in=[PLACEMENT, IELTS],
        )
        Submission.objects.filter(section_link__section_attempt__attempt__in=attempts).delete()
        attempts.delete()
        Exercise.objects.filter(audio_prompt_url__contains=f"/media/{AUDIO_DIR}/").update(
            audio_prompt_url=""
        )
        shutil.rmtree(self.media_root / AUDIO_DIR, ignore_errors=True)

    # ---------------------------------------------------------------- meetings
    def _seed_meetings(self, klass, teacher):
        now = timezone.now()
        tomorrow = (timezone.localtime(now) + timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0,
        )
        specs = [
            (f"{QA}Live speaking practice", dict(
                status=MeetingStatus.ACTIVE, scheduled_at=None,
                started_at=now - timedelta(minutes=12), ended_at=None)),
            (f"{QA}Tutorial — tomorrow 10:00", dict(
                status=MeetingStatus.SCHEDULED, scheduled_at=tomorrow,
                started_at=None, ended_at=None)),
            (f"{QA}Last week's review", dict(
                status=MeetingStatus.ENDED, scheduled_at=None,
                started_at=now - timedelta(days=3, minutes=25),
                ended_at=now - timedelta(days=3))),
        ]
        for title, fields in specs:
            Meeting.objects.update_or_create(
                klass=klass, title=title, defaults={"created_by": teacher, **fields},
            )
        self.stdout.write(f"Meetings: {len(specs)} rooms in '{klass.class_name}'")

    # ------------------------------------------------------------------- inbox
    def _seed_inbox(self, klass, teacher, alice, bob):
        module, _ = LearningModule.objects.get_or_create(
            title=MODULE_TITLE,
            defaults={"description": "Rows created by `manage.py seed_qa` for manual testing.",
                      "created_by": teacher},
        )
        reading, created = Exercise.objects.get_or_create(
            title=READING_TITLE,
            defaults={"module": module, "exercise_type": ExerciseType.READING,
                      "prompt_text": "Read the passage and choose the best answer to each question.",
                      "content_text": READING_PASSAGE, "created_by": teacher},
        )
        if created:
            for order, (text, options) in enumerate(READING_QUESTIONS):
                question = Question.objects.create(exercise=reading, text=text, order=order)
                QuestionOption.objects.bulk_create([
                    QuestionOption(question=question, text=label, is_correct=right, order=i)
                    for i, (label, right) in enumerate(options)
                ])
        writing, _ = Exercise.objects.get_or_create(
            title=WRITING_TITLE,
            defaults={"module": module, "exercise_type": ExerciseType.WRITING,
                      "prompt_text": ("Describe your hometown: where it is, what it is known "
                                      "for, and what you would change about it. Write at "
                                      "least 120 words."),
                      "created_by": teacher},
        )
        due = timezone.now() + timedelta(days=7)
        reading_assignment, _ = Assignment.objects.get_or_create(
            klass=klass, exercise=reading, defaults={"assigned_by": teacher, "due_date": due},
        )
        writing_assignment, _ = Assignment.objects.get_or_create(
            klass=klass, exercise=writing, defaults={"assigned_by": teacher, "due_date": due},
        )

        # bob's reading answers: one right, two wrong — pending in emma's inbox.
        responses = []
        for question, index in zip(reading.questions.order_by("order", "id"), READING_CHOICES):
            option = question.options.order_by("order", "id")[index]
            responses.append({"question_id": question.id, "type": "mcq", "option_id": option.id})
        bob_reading, created = Submission.objects.get_or_create(
            student=bob, exercise=reading,
            defaults={"assignment": reading_assignment,
                      "submission_type": SubmissionType.READING,
                      "answers": {"version": 1, "responses": responses}},
        )
        if created:
            bob_reading.grade()
            bob_reading.save(update_fields=["auto_score"])

        # alice's essay waits for emma; bob's is already graded by her.
        self.alice_essay, _ = Submission.objects.get_or_create(
            student=alice, exercise=writing,
            defaults={"assignment": writing_assignment,
                      "submission_type": SubmissionType.WRITING, "writing_text": ALICE_ESSAY},
        )
        self.bob_essay, created = Submission.objects.get_or_create(
            student=bob, exercise=writing,
            defaults={"assignment": writing_assignment,
                      "submission_type": SubmissionType.WRITING, "writing_text": BOB_ESSAY,
                      "status": SubmissionStatus.GRADED},
        )
        if created:
            Feedback.objects.create(submission=self.bob_essay, reviewer=teacher,
                                    score=Decimal("72"), comments=BOB_FEEDBACK)
        self.bob_reading = bob_reading
        self.stdout.write("Inbox: reading check (bob, 1/3) + essay (alice) pending; bob's essay graded")

    # -------------------------------------------------------------- audio (TTS)
    def _tts(self, text: str, relative: str) -> str | None:
        """Render ``text`` to MEDIA_ROOT/<relative>.wav with macOS `say`; return
        the MEDIA_ROOT-relative name, or None when speech is unavailable."""
        if not self.say:
            return None
        target = self.media_root / f"{relative}.wav"
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            script = target.with_suffix(".txt")
            script.write_text(text)
            base = [self.say, "-r", SPEECH_RATE, "--file-format=WAVE",
                    "--data-format=LEI16@22050", "-o", str(target), "-f", str(script)]
            for cmd in (base[:1] + ["-v", VOICE] + base[1:], base):  # voice, then default
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0 and target.is_file():
                    break
            else:
                script.unlink(missing_ok=True)
                self.stdout.write(self.style.WARNING(f"  say failed for {relative}: {result.stderr.strip()[:120]}"))
                return None
            script.unlink(missing_ok=True)
        return f"{relative}.wav"

    def _signed(self, name: str) -> str:
        return f"{self.api_url}{media.signed_url(name)}"

    def _seed_listening_audio(self):
        if not self.say:
            return
        parts = (Exercise.objects
                 .filter(exercise_type=ExerciseType.LISTENING)
                 .exclude(content_text__isnull=True).exclude(content_text="")
                 .order_by("id"))
        done = 0
        for exercise in parts:
            current = exercise.audio_prompt_url or ""
            if current and f"/media/{AUDIO_DIR}/" not in current:
                continue  # real audio attached by someone — leave it
            name = self._tts(_speech_text(exercise.content_text),
                             f"{AUDIO_DIR}/listening/exercise-{exercise.id}")
            if name is None:
                continue
            exercise.audio_prompt_url = self._signed(name)
            exercise.save(update_fields=["audio_prompt_url"])
            done += 1
            self.stdout.write(f"  audio: {exercise.title[:60]}")
        self.stdout.write(f"Listening audio: {done} parts (links valid "
                          f"{settings.MEDIA_URL_TTL // 86400} days — re-run to refresh)")

    # ---------------------------------------------------------------- attempts
    def _answers(self, exercise, *, wrong_every=3, skip_every=7) -> dict:
        """Envelope in the web client's shape: every third question wrong,
        every seventh left blank, the rest right."""
        responses = []
        questions = exercise.questions.prefetch_related("options").order_by("order", "id")
        for index, question in enumerate(questions):
            if skip_every and index % skip_every == skip_every - 1:
                continue
            wrong = bool(wrong_every) and index % wrong_every == wrong_every - 1
            options = list(question.options.all())
            keys = blank_answer_key(question.text)
            if options:
                right = [o for o in options if o.is_correct]
                others = [o for o in options if not o.is_correct]
                pick = (others or right or options)[0] if wrong else (right or options)[0]
                responses.append({"question_id": question.id, "type": "mcq",
                                  "option_id": pick.id})
            elif keys:
                blanks = ["banana" if wrong else alternatives[0] for alternatives in keys]
                responses.append({"question_id": question.id, "type": "fill_blank",
                                  "text": json.dumps(blanks)})
            else:
                responses.append({"question_id": question.id, "type": "short_answer",
                                  "text": "I am not sure about this one."})
        return {"version": 1, "responses": responses}

    def _writing_for(self, exercise) -> str:
        title = exercise.title.lower()
        if "task 1" in title:
            return IELTS_TASK1
        if "task 2" in title:
            return IELTS_TASK2
        return PLACEMENT_WRITING

    def _recording_for(self, student, exercise) -> str | None:
        for part, script in SPEAKING_SCRIPTS.items():
            if part.lower() in exercise.title.lower():
                name = self._tts(script, f"{AUDIO_DIR}/speaking/{student.email.split('@')[0]}-{exercise.id}")
                return f"{settings.MEDIA_URL}{name}" if name else None
        return None

    def _sit(self, student, template, skills) -> TestAttempt:
        """Sit the given skills of ``template`` in order. AI marking is not
        claimed here; each scenario decides what happens next."""
        with override_settings(MOCK_TEST_AI_AUTOGRADE=False):
            attempt = mock_tests.start_attempt(template, student, mode=AttemptMode.PRACTICE)
            sections = attempt.sections.select_related("section").order_by("section__order", "id")
            for section_attempt in sections:
                skill = section_attempt.section.skill
                if skill not in skills:
                    continue
                mock_tests.start_section(attempt, section_attempt.id)
                draft = {"answers": {}, "writing": {},
                         "meta": {"audio_played": [], "completed_items": []}}
                clips = {}
                items = section_attempt.section.items.select_related("exercise").order_by("order", "id")
                for item in items:
                    exercise = item.exercise
                    key = str(exercise.id)
                    if skill in RECEPTIVE_SKILLS:
                        draft["answers"][key] = self._answers(exercise)
                        draft["meta"]["audio_played"].append(exercise.id)
                    elif skill == SectionSkill.WRITING:
                        draft["writing"][key] = self._writing_for(exercise)
                    else:
                        url = self._recording_for(student, exercise)
                        if url:
                            clips[key] = url
                    draft["meta"]["completed_items"].append(exercise.id)
                mock_tests.submit_section(attempt, section_attempt.id, draft=draft, recordings=clips)
        return attempt

    @staticmethod
    def _mark_with_mock_engines(attempt, section_ids=None) -> list[int]:
        """Deterministic, offline marking: the same code path as production but
        with the mock grading/assist backends, run inline."""
        with override_settings(AI_BACKEND="mock", AI_ASSIST_BACKEND="mock",
                               MOCK_TEST_AI_ASYNC=False):
            ids = mock_tests.claim_ai_grading(attempt, section_ids)
            mock_tests.schedule_ai_grading(ids)
        return ids

    def _seed_attempts(self, students) -> dict:
        placement = MockTestTemplate.objects.get(title=PLACEMENT)
        ielts = MockTestTemplate.objects.get(title=IELTS)
        both = {SectionSkill.READING, SectionSkill.WRITING}
        all_four = set(SectionSkill.values)
        attempts = {}

        def existing(student, template):
            return TestAttempt.objects.filter(student=student, template=template).first()

        # bob — completed, marked by the mock engines.
        attempt = existing(students["bob"], placement)
        if attempt is None:
            attempt = self._sit(students["bob"], placement, both)
            self._mark_with_mock_engines(attempt)
        attempts["bob"] = attempt

        # charlie — completed, marking still pending (the report page will claim it).
        attempt = existing(students["charlie"], placement)
        if attempt is None:
            attempt = self._sit(students["charlie"], placement, both)
        attempts["charlie"] = attempt

        # ethan — Writing marked, Reading failed with a simulated outage.
        attempt = existing(students["ethan"], placement)
        if attempt is None:
            attempt = self._sit(students["ethan"], placement, both)
            writing = attempt.sections.get(section__skill=SectionSkill.WRITING)
            self._mark_with_mock_engines(attempt, [writing.id])
            attempt.sections.filter(section__skill=SectionSkill.READING).update(
                ai_status=AiGradingStatus.FAILED,
                ai_started_at=timezone.now() - timedelta(minutes=2),
                ai_finished_at=timezone.now(),
                ai_error="QA: simulated provider outage — press Retry",
            )
        attempts["ethan"] = attempt

        # fiona — IELTS in progress: receptive sections done, productive untouched.
        attempt = existing(students["fiona"], ielts)
        if attempt is None:
            attempt = self._sit(students["fiona"], ielts,
                                {SectionSkill.LISTENING, SectionSkill.READING})
        attempts["fiona"] = attempt

        # george — IELTS complete, all four skills marked by the mock engines.
        attempt = existing(students["george"], ielts)
        if attempt is None:
            attempt = self._sit(students["george"], ielts, all_four)
            self._mark_with_mock_engines(attempt)
        attempts["george"] = attempt

        for name, attempt in attempts.items():
            attempt.refresh_from_db()
            state = ", ".join(f"{s.section.skill}:{s.status}/{s.ai_status}"
                              for s in attempt.sections.select_related("section"))
            self.stdout.write(f"Attempt {attempt.id:>3} {name:<8} {attempt.template.title[:28]:<28} {state}")
        return attempts

    # ------------------------------------------------------------------- guide
    def _print_guide(self, students, klass):
        web = "http://localhost:3000"
        a = self.attempts
        self.stdout.write(self.style.SUCCESS("\n=== QA data ready (password: password123) ==="))
        self.stdout.write(f"""
Meetings ({web}/meetings)
  student alice.student@english.app  -> live room to join, tomorrow's booking, an ended call
  teacher {TEACHER}   -> same rooms with End / Cancel; "Start meeting" creates more

Practice catalog ({web}/exercises)   any login; filters by band / topic / skill

Teacher screen ({web}/submissions, {TEACHER})
  #{self.alice_essay.id}  alice's essay (writing, pending)        -> grade; no "Explain my mistakes" card
  #{self.bob_reading.id}  bob's reading check (1 of 3, pending)   -> answers review; no card
Student view ({web}/submissions/{self.bob_essay.id}, bob.student@english.app)
  bob's graded essay -> teacher feedback + "Explain my mistakes" card (student only)

Mock tests ({web}/mock-tests; report = {web}/mock-tests/attempts/<id>/report)
  bob      #{a['bob'].id:<3} Placement  fully marked (mock engines)  -> per-question review, AI feedback
  charlie  #{a['charlie'].id:<3} Placement  marking pending              -> report page claims it; live provider runs
  ethan    #{a['ethan'].id:<3} Placement  Reading failed               -> Retry button (live provider)
  fiona    #{a['fiona'].id:<3} IELTS      in progress                  -> resume banner, partial report
  george   #{a['george'].id:<3} IELTS      4 skills marked (mock)       -> listening review, spoken answers
  Listening parts of the IELTS / TOEIC tests now play synthesised audio (play-once player).
  Teacher {TEACHER} can open every report above (all five are in '{klass.class_name}').

Live AI marking uses the providers in .env (NIM models queue for minutes;
set AI_GRADING_MODEL=gemini and AI_ASSIST_MODEL=gemini for interactive speed).
Re-run `manage.py seed_qa` any time (refreshes signed audio links); `--reset` recreates everything.
""")
