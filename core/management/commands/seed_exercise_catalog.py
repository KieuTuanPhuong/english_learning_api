"""Seed the browsable practice catalog: exercises tagged by topic + band.

    python manage.py seed_exercise_catalog

Idempotent — keyed on Exercise.title via update_or_create, so re-running
refreshes content instead of duplicating it. Questions are rebuilt each run
(options carry no stable key of their own).

These are standalone practice items: `module` is left null on purpose. They are
what /api/exercises/?topic=&band= serves to the student browse page, and they
exist independently of any teacher's module or class.

Productive types (writing/speaking) need only a prompt. Receptive types carry
their stimulus on the Exercise (reading -> content_text passage) plus questions
with an answer key — which is why the catalog list endpoint never embeds them.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import (
    BandLevel,
    Exercise,
    ExerciseType,
    Question,
    QuestionOption,
    Topic,
    User,
    UserRole,
)

W, S, R, Q = (
    ExerciseType.WRITING,
    ExerciseType.SPEAKING,
    ExerciseType.READING,
    ExerciseType.QUIZ,
)
B45, B56, B67, B78, B89 = (
    BandLevel.BAND_4_5,
    BandLevel.BAND_5_6,
    BandLevel.BAND_6_7,
    BandLevel.BAND_7_8,
    BandLevel.BAND_8_9,
)

# ---------------------------------------------------------------- passages
CITY_ROUTINES = (
    "Before the nineteenth century, most people worked to the rhythm of daylight "
    "and weather. Farm work expanded in summer and shrank in winter, and a worker "
    "who finished early simply went home. The factory changed that arrangement. "
    "Because expensive machinery had to run continuously to repay its cost, owners "
    "needed workers to arrive together and stay for a fixed period. The clock, not "
    "the sun, began to divide the day.\n\n"
    "This new discipline spread far beyond the factory gate. Railway timetables "
    "forced towns to abandon their own local time; schools adopted bells and "
    "periods; even meals moved to fixed hours so that families could eat together "
    "around a shared shift. Historians note that the complaint we now make about "
    "cities — that life there feels rushed — appeared in print almost as soon as "
    "the fixed working day did.\n\n"
    "What has changed recently is not the length of the working day but its "
    "boundaries. Remote work has returned some control over when tasks are done, "
    "while making it harder to say when work has stopped."
)

SPACED_LEARNING = (
    "In the 1880s Hermann Ebbinghaus memorised long lists of invented syllables "
    "and tested himself at intervals, plotting how quickly his memory decayed. His "
    "curve was steep: most of what he learned disappeared within days. But he also "
    "noticed something more useful. When he spread his study sessions across "
    "several days rather than massing them into one, the same total study time "
    "produced far more durable memories.\n\n"
    "A century of research has confirmed this spacing effect across subjects and "
    "ages. The mechanism is still debated. One account holds that a partly "
    "forgotten memory requires more effort to retrieve, and that this effort is "
    "what strengthens it. Another emphasises that separate sessions occur in "
    "different contexts, giving the memory more routes back.\n\n"
    "The practical implication is uncomfortable for students, because massed study "
    "feels more effective while it is happening. Fluency during a single long "
    "session is mistaken for learning, and the slower, harder spaced schedule is "
    "abandoned just as it begins to pay off."
)

RIVER_REWILDING = (
    "Europe has more than a million barriers across its rivers — weirs, culverts "
    "and dams, many of them obsolete. They were built to power mills, supply "
    "towns, or control floods, and a large share no longer serve any purpose. "
    "Their effect on fish is severe: migratory species such as salmon and eel "
    "cannot reach spawning grounds, and populations upstream become isolated.\n\n"
    "Removal is now the preferred response where a barrier has no current use. "
    "When a dam comes down, sediment held behind it moves downstream and rebuilds "
    "gravel beds, and fish typically return within a season or two — often faster "
    "than ecologists predict. Costs are usually lower than maintaining an ageing "
    "structure, which is what persuades many owners.\n\n"
    "The obstacles are rarely technical. Records of who owns a small weir may not "
    "exist, and communities often value a structure that has been part of the "
    "landscape for generations. Projects that begin with consultation, rather than "
    "engineering, are the ones that finish."
)

# ------------------------------------------------------- catalog definition
# (topic, band, type, title, prompt, content_text, questions)
# questions: [(text, [(option, is_correct), ...]), ...]
CATALOG = [
    # ---------------- Life
    (Topic.LIFE, B45, S, "Describe your daily routine",
     "Talk for one to two minutes about a normal weekday. Say when you get up, "
     "what you do in the morning, afternoon and evening, and which part of the "
     "day you enjoy most.", None, None),
    (Topic.LIFE, B56, W, "A letter about moving to a new home",
     "A friend has asked how your move went. Write 120–150 words describing your "
     "new home, explaining what you like about the area, and inviting them to "
     "visit.", None, None),
    (Topic.LIFE, B67, W, "Essay: living alone or with family",
     "Some people believe young adults should live independently as soon as they "
     "can; others think staying with family is better. Discuss both views and "
     "give your own opinion. Write at least 250 words.", None, None),
    (Topic.LIFE, B78, R, "Reading: how cities changed the working day",
     "Read the passage and answer the questions that follow.",
     CITY_ROUTINES,
     [
         ("According to the passage, why did factory owners want fixed hours?",
          [("Expensive machinery had to run continuously to repay its cost", True),
           ("Workers had asked for a predictable schedule", False),
           ("Daylight was unreliable inside factory buildings", False),
           ("Railway timetables required it by law", False)]),
         ("What does the passage say about complaints that city life feels rushed?",
          [("They appeared in print almost as soon as the fixed working day did", True),
           ("They began only after remote work became common", False),
           ("Historians consider them unfounded", False),
           ("They were first made by factory owners", False)]),
         ("What does the writer identify as the recent change?",
          [("The boundaries of the working day rather than its length", True),
           ("A significant shortening of the working day", False),
           ("A return to working by daylight", False),
           ("The abandonment of local time by towns", False)]),
     ]),

    # ---------------- Sports
    (Topic.SPORTS, B45, S, "Describe a sport you enjoy",
     "Talk for one to two minutes about a sport you play or watch. Say how you "
     "started, how often you take part, and why you like it.", None, None),
    (Topic.SPORTS, B56, W, "A message about joining a team",
     "You want to join a local sports club. Write 120–150 words to the organiser "
     "saying which sport you want to play, describing your experience, and asking "
     "about training times and fees.", None, None),
    (Topic.SPORTS, B67, W, "Essay: money in professional sport",
     "Top athletes are paid far more than people in most other professions. Some "
     "argue this is fair; others think it is unjustified. Discuss both views and "
     "give your own opinion. Write at least 250 words.", None, None),
    (Topic.SPORTS, B78, Q, "Vocabulary check: talking about sport",
     "Choose the option that completes each sentence most naturally. These are "
     "collocations examiners expect at a high band.", None,
     [
         ("After six months out with a knee injury, she is finally ___ to full fitness.",
          [("back", True), ("returned", False), ("recovered", False), ("arrived", False)]),
         ("The team ___ a comfortable victory in the second half.",
          [("secured", True), ("won over", False), ("gained up", False), ("made", False)]),
         ("Critics argue that hosting the tournament placed an enormous ___ on public funds.",
          [("strain", True), ("stress", False), ("tension", False), ("weight", False)]),
         ("He holds the national ___ for the 400 metres.",
          [("record", True), ("result", False), ("score", False), ("mark", False)]),
     ]),

    # ---------------- Education
    (Topic.EDUCATION, B45, S, "Describe a teacher you remember",
     "Talk for one to two minutes about a teacher who made an impression on you. "
     "Say what they taught, what they were like, and why you remember them.",
     None, None),
    (Topic.EDUCATION, B56, W, "An email about a missed class",
     "You missed an important class. Write 120–150 words to your teacher "
     "explaining why you were absent, asking what was covered, and requesting the "
     "materials.", None, None),
    (Topic.EDUCATION, B67, W, "Essay: should university be free?",
     "Some people think university education should be funded entirely by the "
     "state; others believe students should pay. Discuss both views and give your "
     "own opinion. Write at least 250 words.", None, None),
    (Topic.EDUCATION, B78, R, "Reading: the spacing effect",
     "Read the passage and answer the questions that follow.",
     SPACED_LEARNING,
     [
         ("What did Ebbinghaus find when he spread study across several days?",
          [("The same total study time produced more durable memories", True),
           ("He forgot material more quickly than before", False),
           ("Memory decayed at a constant rate", False),
           ("Longer sessions were needed to reach the same result", False)]),
         ("Which explanation of the spacing effect does the passage mention?",
          [("Retrieving a partly forgotten memory takes effort that strengthens it", True),
           ("Sleep between sessions removes irrelevant details", False),
           ("Repetition builds confidence, which aids recall", False),
           ("Shorter sessions reduce fatigue", False)]),
         ("Why does the writer call the implication 'uncomfortable'?",
          [("Massed study feels more effective while it is happening", True),
           ("Spaced study requires more total hours", False),
           ("Teachers rarely allow spaced schedules", False),
           ("The research applies only to invented syllables", False)]),
     ]),
    (Topic.EDUCATION, B89, W, "Essay: assessment beyond examinations",
     "Written examinations remain the dominant form of assessment, yet critics "
     "argue they measure performance under pressure rather than understanding. "
     "Evaluate this claim and consider what a credible alternative would require. "
     "Write at least 280 words.", None, None),

    # ---------------- Work
    (Topic.WORK, B45, S, "Describe your job or the job you want",
     "Talk for one to two minutes about your work, or the work you hope to do. "
     "Say what the job involves, what skills it needs, and why it appeals to you.",
     None, None),
    (Topic.WORK, B56, W, "A request to change your working hours",
     "Write 120–150 words to your manager asking to change your working hours. "
     "Explain your reason, propose new hours, and say how your work will be "
     "covered.", None, None),
    (Topic.WORK, B67, W, "Essay: working from home",
     "Many companies now allow staff to work from home. Do the advantages of this "
     "outweigh the disadvantages? Give reasons and examples. Write at least 250 "
     "words.", None, None),
    (Topic.WORK, B78, S, "Discussion: automation and employment",
     "Speak for two minutes on this question, then extend your answer: which "
     "kinds of work are hardest to automate, and what should governments do for "
     "workers whose jobs disappear? Justify your view with examples.", None, None),

    # ---------------- Travel
    (Topic.TRAVEL, B45, S, "Describe a journey you enjoyed",
     "Talk for one to two minutes about a journey you remember. Say where you "
     "went, who you travelled with, and what made it enjoyable.", None, None),
    (Topic.TRAVEL, B56, W, "A postcard-style description of a place",
     "Write 120–150 words describing a place you have visited: what it looks "
     "like, what you did there, and whether you would recommend it.", None, None),
    (Topic.TRAVEL, B67, W, "Essay: the cost of mass tourism",
     "Tourism brings income to many regions but can damage the places visitors "
     "come to see. Discuss the benefits and drawbacks and give your own opinion. "
     "Write at least 250 words.", None, None),
    (Topic.TRAVEL, B78, S, "Discussion: travel and cultural understanding",
     "Speak for two minutes: does travel genuinely increase understanding between "
     "cultures, or mainly confirm what travellers already believe? Support your "
     "answer with examples.", None, None),

    # ---------------- Environment
    (Topic.ENVIRONMENT, B45, S, "Describe the weather where you live",
     "Talk for one to two minutes about the climate in your area. Describe the "
     "seasons, which you prefer, and how the weather affects daily life.",
     None, None),
    (Topic.ENVIRONMENT, B56, W, "A letter about local recycling",
     "Write 120–150 words to your local council about recycling in your area. "
     "Describe the current situation, explain one problem, and suggest an "
     "improvement.", None, None),
    (Topic.ENVIRONMENT, B67, W, "Essay: individual action or government policy?",
     "Some argue that solving environmental problems depends on individual "
     "choices; others say only government regulation can work. Discuss both views "
     "and give your own opinion. Write at least 250 words.", None, None),
    (Topic.ENVIRONMENT, B78, R, "Reading: rewilding Europe's rivers",
     "Read the passage and answer the questions that follow.",
     RIVER_REWILDING,
     [
         ("What does the passage say about many of Europe's river barriers?",
          [("A large share no longer serve any purpose", True),
           ("Most were built within the last fifty years", False),
           ("They are generally cheap to maintain", False),
           ("They were designed with fish passage in mind", False)]),
         ("What typically happens after a dam is removed?",
          [("Sediment moves downstream and rebuilds gravel beds", True),
           ("Water quality declines for several years", False),
           ("Fish populations recover more slowly than predicted", False),
           ("Flooding downstream becomes more frequent", False)]),
         ("According to the passage, what makes removal projects succeed?",
          [("Beginning with consultation rather than engineering", True),
           ("Securing government funding in advance", False),
           ("Removing several barriers simultaneously", False),
           ("Replacing old structures with modern ones", False)]),
     ]),
    (Topic.ENVIRONMENT, B89, W, "Essay: the limits of climate targets",
     "National climate targets are frequently announced and frequently missed. "
     "Assess why the gap between commitment and delivery persists, and what would "
     "make targets credible. Write at least 280 words.", None, None),

    # ---------------- Technology
    (Topic.TECHNOLOGY, B45, S, "Describe a device you use every day",
     "Talk for one to two minutes about a device you rely on. Say what it does, "
     "how often you use it, and how life would differ without it.", None, None),
    (Topic.TECHNOLOGY, B56, W, "A review of an app you use",
     "Write 120–150 words reviewing an app or website you use often. Say what it "
     "is for, what works well, and one thing you would change.", None, None),
    (Topic.TECHNOLOGY, B67, W, "Essay: children and screen time",
     "Some people believe children's screen time should be strictly limited; "
     "others think restrictions are unnecessary. Discuss both views and give your "
     "own opinion. Write at least 250 words.", None, None),
    (Topic.TECHNOLOGY, B78, Q, "Vocabulary check: describing technology",
     "Choose the option that completes each sentence most naturally.", None,
     [
         ("The new system was ___ out across all offices over six months.",
          [("rolled", True), ("turned", False), ("brought", False), ("put", False)]),
         ("Older machines are gradually being ___ by cloud services.",
          [("superseded", True), ("overtaken up", False), ("succeeded on", False),
           ("replaced off", False)]),
         ("Engineers had to ___ a workaround before the deadline.",
          [("devise", True), ("invent up", False), ("conceive out", False),
           ("fabricate on", False)]),
         ("The interface is intuitive, so training requirements are ___.",
          [("minimal", True), ("few", False), ("scarce", False), ("slight", False)]),
     ]),
    (Topic.TECHNOLOGY, B89, W, "Essay: regulating artificial intelligence",
     "Governments are drafting rules for artificial intelligence while the "
     "technology is still changing. Assess the difficulties this creates and "
     "argue for the approach you consider most defensible. Write at least 280 "
     "words.", None, None),

    # ---------------- Health
    (Topic.HEALTH, B45, S, "Describe how you stay healthy",
     "Talk for one to two minutes about what you do to stay healthy: food, "
     "exercise and sleep. Say what you find easy and what you find difficult.",
     None, None),
    (Topic.HEALTH, B56, W, "A note about a medical appointment",
     "Write 120–150 words to a clinic. Explain your symptoms, say when you are "
     "available, and ask what you should bring to the appointment.", None, None),
    (Topic.HEALTH, B67, W, "Essay: who should pay for healthcare?",
     "Some believe healthcare should be free for everyone at the point of use; "
     "others think individuals should contribute. Discuss both views and give "
     "your own opinion. Write at least 250 words.", None, None),
    (Topic.HEALTH, B78, Q, "Vocabulary check: health and wellbeing",
     "Choose the option that completes each sentence most naturally.", None,
     [
         ("Regular exercise substantially reduces the ___ of heart disease.",
          [("risk", True), ("danger", False), ("threat", False), ("hazard", False)]),
         ("She made a full ___ after several weeks of rest.",
          [("recovery", True), ("healing", False), ("cure", False), ("repair", False)]),
         ("Public health campaigns aim to ___ awareness of early symptoms.",
          [("raise", True), ("rise", False), ("lift up", False), ("grow on", False)]),
         ("The treatment ___ the symptoms but does not address the cause.",
          [("alleviates", True), ("alleviates for", False), ("soothes down", False),
           ("comforts up", False)]),
     ]),

    # ---------------- Culture
    (Topic.CULTURE, B45, S, "Describe a festival you celebrate",
     "Talk for one to two minutes about a festival or celebration. Say when it "
     "happens, what people do, and what you enjoy about it.", None, None),
    (Topic.CULTURE, B56, W, "A description of a traditional meal",
     "Write 120–150 words about a traditional dish from your country: what is in "
     "it, when it is eaten, and why it matters to people.", None, None),
    (Topic.CULTURE, B67, W, "Essay: preserving traditional culture",
     "Some argue that governments should spend money preserving traditional "
     "culture; others believe the money is better spent elsewhere. Discuss both "
     "views and give your own opinion. Write at least 250 words.", None, None),
    (Topic.CULTURE, B78, S, "Discussion: global media and local identity",
     "Speak for two minutes: does global film and music weaken local cultural "
     "identity, or give it a wider audience? Support your answer with examples.",
     None, None),
]


class Command(BaseCommand):
    help = "Seed the topic/band-tagged practice exercise catalog (idempotent)."

    @transaction.atomic
    def handle(self, *args, **options):
        # Attribute the catalog to an admin when one exists, so rows have a
        # plausible author without pretending a particular teacher wrote them.
        author = (
            User.objects.filter(role=UserRole.ADMIN).order_by("id").first()
            or User.objects.filter(role=UserRole.TEACHER).order_by("id").first()
        )

        created = updated = 0
        for topic, band, ex_type, title, prompt, content, questions in CATALOG:
            exercise, was_created = Exercise.objects.update_or_create(
                title=title,
                defaults={
                    "exercise_type": ex_type,
                    "band": band,
                    "topic": topic,
                    "prompt_text": prompt,
                    "content_text": content,
                    "created_by": author,
                    "module": None,
                },
            )
            created += was_created
            updated += not was_created

            # Rebuilt rather than patched: options carry no stable key.
            exercise.questions.all().delete()
            for q_index, (q_text, options) in enumerate(questions or [], start=1):
                question = Question.objects.create(
                    exercise=exercise, text=q_text, order=q_index
                )
                QuestionOption.objects.bulk_create([
                    QuestionOption(
                        question=question, text=text, is_correct=is_correct, order=o
                    )
                    for o, (text, is_correct) in enumerate(options, start=1)
                ])

        topics = len({row[0] for row in CATALOG})
        bands = len({row[1] for row in CATALOG})
        self.stdout.write(
            self.style.SUCCESS(
                f"Catalog ready: {created} created, {updated} refreshed "
                f"({len(CATALOG)} exercises across {topics} topics, {bands} bands)."
            )
        )
