"""Stock the mock-test library with ready-to-take tests.

    python manage.py seed_mock_tests

Mock tests are app content, not classroom content (see MockTestTemplate's
docstring), so the platform ships with a shelf that is not empty. Idempotent:
re-running updates the same rows rather than duplicating them, and a test that
students have already sat is left alone.

The library carries two formats: **IELTS Academic**, at the real exam's shape
and length, and **Custom**, an in-house percentage-scored test that exists so
staff have a worked example to copy when they author their own. TOEIC was
retired (``seed_test_formats``); its rows survive so old attempts still convert.

The IELTS paper follows the published structure:

* **Listening** — four recordings, 40 questions, 30 minutes. Parts run in order
  and do not reopen (``item_flow="sequential"``), because the real recording
  plays continuously.
* **Reading** — three passages, 40 questions, 60 minutes, all passages available
  at once, which is how the real paper works.
* **Writing** — two tasks in one 60-minute block. The 20/40 minute split is
  advice in the real exam, not a rule, so it is printed in the instructions
  rather than enforced by two timers. Task 2 carries ``weight=2``: the section
  band is (Task 1 + 2 x Task 2) / 3.
* **Speaking** — three parts, taken in order. Part 2 gives a minute of
  preparation and up to two minutes of talk; Parts 1 and 3 are longer and
  unprepared.

Two honest limitations, both stated to the student in the test description:

1. **No examiner.** Speaking is recorded and graded afterwards, not conducted
   face to face. The three-part structure, timings and prompts are real; the
   interaction is not.
2. **Audio is optional.** ``Exercise.audio_prompt_url`` is uploadable now
   (``POST /api/media/audio``), but the seeder ships no media files, so
   Listening parts carry the transcript in ``content_text`` and are answerable
   today. Attach a URL and the play-once player takes over with no other change.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import (
    DifficultyLevel,
    Exercise,
    ExerciseType,
    ItemFlow,
    LearningModule,
    MockTestTemplate,
    Question,
    QuestionOption,
    SectionSkill,
    TestFormat,
    TestSection,
    TestSectionExercise,
)

MODULE_TITLE = "Mock test content"

NO_EXAMINER_NOTE = (
    "Speaking is recorded and graded afterwards rather than conducted with an "
    "examiner. The structure, timings and prompts follow the real test."
)
AUDIO_PENDING = (
    "Audio playback is not available for this paper — read the transcript and "
    "answer as you would after listening once."
)


# --- question helpers -------------------------------------------------------
def mcq(text, options, correct_index):
    """Multiple choice: `options` is a list of strings, one of them correct."""
    return {"text": text, "options": options, "correct": correct_index}


def tfng(text, verdict):
    """IELTS True / False / Not Given. `verdict` is "T", "F" or "NG"."""
    return {
        "text": text,
        "options": ["True", "False", "Not Given"],
        "correct": {"T": 0, "F": 1, "NG": 2}[verdict],
    }


def ynng(text, verdict):
    """IELTS Yes / No / Not Given — claims about the writer's own views."""
    return {
        "text": text,
        "options": ["Yes", "No", "Not Given"],
        "correct": {"Y": 0, "N": 1, "NG": 2}[verdict],
    }


def blank(text, max_words=None):
    """Fill-in-the-blank using the platform's `[[answer]]` convention. The runner
    serializer masks the key to `[[]]` before it reaches the browser.

    A key may list alternates with `|` — `[[colour|color]]` — and `max_words`
    applies the exam's "NO MORE THAN N WORDS" rubric.
    """
    return {"text": text, "options": [], "correct": None, "max_words": max_words}


# ============================================================ IELTS LISTENING
# Part 1 — a transactional conversation. Form completion, ten answers.
IELTS_LISTENING_1_TRANSCRIPT = """\
PART 1 — TRANSCRIPT

You will hear a telephone conversation between a student and an assistant at a
student housing agency. First you have some time to look at questions 1 to 10.

ASSISTANT: Good morning, Brightwater Student Housing, Nadia speaking.

CALLER: Oh, hello. I'm hoping to find a flat for the coming academic year.

ASSISTANT: Certainly. Let me take a few details. Could I start with your name?

CALLER: It's Helena Voss.

ASSISTANT: Could you spell the surname for me?

CALLER: V-O-S-S. Two S's at the end.

ASSISTANT: Thank you. And what sort of budget did you have in mind?

CALLER: I've worked it out fairly carefully. I can manage up to 320 pounds a
month, including bills if that's possible, but 320 is the absolute ceiling.

ASSISTANT: That's workable in the north of the city. When would you want to
move in?

CALLER: The 14th of September, ideally. My course starts on the 28th, but I'd
like a fortnight to settle before teaching begins.

ASSISTANT: Noted. Any particular requirements about the property itself?

CALLER: Yes — this is the important one. I have asthma, and stairs are hard
work when the weather turns. So it needs to be a ground floor flat. I've been
told that rules a lot of places out, but I'd rather be honest about it now than
waste everyone's time.

ASSISTANT: That's helpful, actually. Ground floor properties go quickly, so
we'll flag your file. Now, our standard deposit is six weeks' rent, which at
your budget comes to 450 pounds. That's held by a government-backed scheme, not
by us, and it's returned within ten days of the final inspection.

CALLER: 450. Fine. Could I ask about transport? I don't drive.

ASSISTANT: Most of our northern properties are walkable to Fernhill station.
That's the nearest station for the whole area, about eight minutes on foot, and
there's a direct line to the university.

CALLER: Fernhill. Good.

ASSISTANT: Could I take a contact number?

CALLER: Yes, it's 07700 900612.

ASSISTANT: Let me read that back — 07700 900612. And is there anything else you
need the property to have?

CALLER: I'd want somewhere to keep a bicycle. Not a garage, just secure storage
of some kind. I cycle when the air is clear.

ASSISTANT: Bicycle storage, noted. Most of the newer conversions have a shed.
Now, our contracts in this area run for 9 months rather than the usual twelve,
which suits students who go home over the summer. Is that acceptable?

CALLER: Nine months is perfect, actually.

ASSISTANT: Last thing. Before we can confirm any viewing, we need a reference
from your previous landlord. Not a character reference — specifically a
landlord reference confirming rent was paid. If you've never rented, a guarantor
form replaces it.

CALLER: I rented last year, so that's straightforward.

ASSISTANT: Then I'll email you three properties this afternoon.
"""

IELTS_LISTENING_1_QUESTIONS = [
    blank("Surname: [[Voss]]", max_words=1),
    blank("Maximum monthly rent: £[[320]]", max_words=1),
    blank(
        "Preferred move-in date: [[14 September|14th September|September 14]]",
        max_words=2,
    ),
    blank("Property requirement: a [[ground floor|ground-floor]] flat", max_words=2),
    blank("Deposit payable: £[[450]]", max_words=1),
    blank("Nearest station: [[Fernhill]]", max_words=1),
    blank("Contact number: [[07700 900612]]", max_words=2),
    blank("Secure storage required for a [[bicycle|bike]]", max_words=1),
    blank("Contract length: [[9 months|nine months]]", max_words=2),
    blank(
        "Must provide a [[landlord reference|reference]] before viewing",
        max_words=2,
    ),
]

# Part 2 — a monologue on an everyday social topic. Multiple choice.
IELTS_LISTENING_2_TRANSCRIPT = """\
PART 2 — TRANSCRIPT

You will hear a talk given to new members of a community sports centre.

Welcome, everyone, and thank you for joining the Lockwood Community Sports
Centre. I'm Priya, the membership coordinator, and this talk should take about
ten minutes.

Let me start with the building, because people get lost in it. We're on three
levels. The ground floor is reception, the café and the two squash courts. The
first floor is the gym and the studios. The pool is in the basement — which
surprises people, but it was the cheapest place to put a very heavy thing.

Opening hours have changed this year and I want to be clear about it. We used to
open at six in the morning on weekdays. From this month we open at half past
five, because a survey of members put early swimming well ahead of every other
request. Closing time is unchanged at ten in the evening. Weekends are eight
until eight.

Now, the pool. There are three lanes, and they are not first-come-first-served.
The left-hand lane is always slow, the middle is medium, the right is fast. The
single most common complaint we receive is about people in the wrong lane, so
please be honest with yourself rather than ambitious.

Booking. Everything except the gym must be booked. The gym you simply walk into.
Squash courts, studio classes and lane swimming all need booking, and the window
opens exactly seven days ahead at seven in the morning. Popular classes go in
under a minute, so set an alarm if you care.

A word about cancellations, because this is where members lose money. If you
cancel more than twelve hours ahead, you are refunded in full. Between twelve
and two hours, you are charged half. Under two hours, or if you simply don't
turn up, you are charged the full amount. We introduced this reluctantly after
a year in which nearly a fifth of booked classes ran with empty places.

Equipment. Rackets are free to borrow — leave your membership card at reception
and collect it when you return them. Towels cost a pound. Lockers are free but
you must supply your own padlock; the ones left overnight are cut off on the
first Monday of each month and the contents go to a charity shop.

Parking is the one thing I can't help you with. We have eleven spaces and nine
hundred members. There is a multi-storey two streets away, and members get a
discount there if you show your card on exit. Cycling is genuinely faster at
peak times, and the racks by the side entrance are covered.

Finally, the children's programme. Lessons run on Saturday mornings only, and
there is a waiting list. Do put your name down early — the list moves in
September when the older group ages out, and hardly at all the rest of the year.
"""

IELTS_LISTENING_2_QUESTIONS = [
    mcq(
        "Where is the swimming pool located?",
        ["On the ground floor", "On the first floor", "In the basement"],
        2,
    ),
    mcq(
        "What time does the centre now open on weekdays?",
        ["05:30", "06:00", "08:00"],
        0,
    ),
    mcq(
        "Why were the weekday opening hours changed?",
        [
            "A member survey showed demand for early swimming",
            "The council required longer opening",
            "Staff shifts were reorganised",
        ],
        0,
    ),
    mcq(
        "How are the three swimming lanes allocated?",
        [
            "First come, first served",
            "By speed — slow, medium and fast",
            "By booking reference number",
        ],
        1,
    ),
    mcq(
        "Which facility does NOT need to be booked?",
        ["The gym", "The squash courts", "Lane swimming"],
        0,
    ),
    mcq(
        "When does the booking window open?",
        [
            "Seven days ahead, at 07:00",
            "Fourteen days ahead, at 09:00",
            "Three days ahead, at midday",
        ],
        0,
    ),
    mcq(
        "A class cancelled nine hours in advance is charged at:",
        ["Nothing", "Half the fee", "The full fee"],
        1,
    ),
    mcq(
        "What must members provide themselves in order to use a locker?",
        ["A padlock", "A towel", "A deposit"],
        0,
    ),
    mcq(
        "What does the speaker say about parking?",
        [
            "Spaces are reserved for members who book",
            "There are far too few spaces, but a nearby car park gives a discount",
            "Parking is free after six in the evening",
        ],
        1,
    ),
    mcq(
        "When does the children's waiting list move significantly?",
        ["In September", "At the start of each term", "Every Saturday"],
        0,
    ),
]

# Part 3 — a discussion in an academic context. Multiple choice.
IELTS_LISTENING_3_TRANSCRIPT = """\
PART 3 — TRANSCRIPT

You will hear two students, Daniel and Mei, discussing their research project
with their tutor, Dr Okafor.

DR OKAFOR: So — the food waste project. Where have you got to?

DANIEL: We've collected the household diaries. Sixty households, four weeks
each. The problem is that the data doesn't say what we expected.

DR OKAFOR: Say more.

DANIEL: We assumed the biggest driver of waste would be overbuying. It isn't.
The households that waste most are the ones that shop most often — small trips,
several times a week.

MEI: Which sounds backwards until you look at why. They're buying without a
plan. Someone who does one big weekly shop has to think ahead. Someone who drops
in daily buys what looks appealing that evening.

DR OKAFOR: That's an interesting inversion. Is it statistically solid?

MEI: That's our worry. Sixty households isn't many, and the effect is clear but
not enormous. I'd rather present it as a hypothesis worth testing than as a
finding.

DANIEL: I disagree slightly. I think we're being too cautious. The pattern is
consistent across every income band we sampled.

DR OKAFOR: You're both partly right, and the disagreement is the useful part.
Daniel, consistency across subgroups is genuine evidence. Mei, sixty is small.
Write it up as a finding, state the sample size prominently, and let the reader
weigh it. What you must not do is bury the number.

MEI: That's fair.

DR OKAFOR: What about the diaries themselves? Self-reported data worries me.

DANIEL: It worried us too. So we did something we're rather pleased with — we
weighed the bins for a subset. Twelve households, one week, actual weights
against reported weights.

DR OKAFOR: And?

DANIEL: People under-report by about a fifth. Consistently. Which is annoying
but usable, because it's a consistent bias rather than random noise.

DR OKAFOR: That subset is the most valuable thing you've done, and I suspect
you don't realise it. Lead with it. A study that measures its own error is worth
three that don't.

MEI: We'd put it in an appendix.

DR OKAFOR: Move it. Chapter two.

MEI: What about the interviews? We've done eighteen and we planned twenty-five.

DR OKAFOR: Are you still learning anything new from them?

MEI: Honestly, no. The last four said more or less what the previous ten did.

DR OKAFOR: Then stop. That's saturation, and it's a legitimate reason to stop —
provided you say so explicitly and explain how you judged it. Continuing to
twenty-five to hit a round number is not research, it's decoration.

DANIEL: One more thing. The deadline. We're presenting on the ninth?

DR OKAFOR: The eleventh. It moved because the room was double-booked. You'll get
an email, but assume the eleventh from now on. And send me chapter two by the
fourth — not the whole draft, just chapter two.
"""

IELTS_LISTENING_3_QUESTIONS = [
    mcq(
        "What did the students expect to be the main cause of food waste?",
        ["Buying too much at once", "Poor storage", "Confusing date labels"],
        0,
    ),
    mcq(
        "According to Mei, why do frequent shoppers waste more?",
        [
            "They shop without planning ahead",
            "They buy lower-quality food",
            "They have smaller refrigerators",
        ],
        0,
    ),
    mcq(
        "What is Mei's concern about the finding?",
        [
            "The sample is small",
            "The diaries were completed incorrectly",
            "The effect appears only in one income band",
        ],
        0,
    ),
    mcq(
        "What does Dr Okafor advise about the sample size?",
        [
            "Collect more households before writing",
            "Report the finding but state the sample size prominently",
            "Leave the finding out of the report",
        ],
        1,
    ),
    mcq(
        "How did the students check the reliability of the diaries?",
        [
            "They weighed the bins of a subset of households",
            "They repeated the diaries a second month",
            "They compared results with a published study",
        ],
        0,
    ),
    mcq(
        "What did that check reveal?",
        [
            "Households under-report waste by roughly 20%",
            "Households over-report waste at random",
            "The diaries were accurate",
        ],
        0,
    ),
    mcq(
        "Why does Dr Okafor value that check so highly?",
        [
            "It measures the study's own error",
            "It was inexpensive to carry out",
            "It uses a standard published method",
        ],
        0,
    ),
    mcq(
        "Where does Dr Okafor want the bin-weighing section placed?",
        ["In an appendix", "In chapter two", "In the conclusion"],
        1,
    ),
    mcq(
        "What does Dr Okafor say about stopping the interviews at eighteen?",
        [
            "It is acceptable if they explain how they judged saturation",
            "They must complete all twenty-five as planned",
            "They should replace interviews with a survey",
        ],
        0,
    ),
    mcq(
        "When is the presentation now scheduled?",
        ["The fourth", "The ninth", "The eleventh"],
        2,
    ),
]

# Part 4 — an academic monologue. Note completion, ten answers.
IELTS_LISTENING_4_TRANSCRIPT = """\
PART 4 — TRANSCRIPT

You will hear part of a lecture on the history and science of urban street
lighting.

Good afternoon. Today I want to trace street lighting from a civic curiosity to
a piece of infrastructure we no longer notice — and to argue that each shift was
driven less by technology than by anxiety.

The earliest organised street lighting in Europe was not electric, nor even gas.
In seventeenth-century Paris, householders on certain streets were legally
obliged to hang a lantern outside after dark. The fuel was oil, the light was
poor, and the motivation was crime. Notice that the first lighting law was a
policing measure, not an amenity.

Gas changed the scale. The first public gas-lit street was Pall Mall in London,
in 1807. Gas allowed a central works to supply a whole district, and that
centralisation is the important part: for the first time a city could decide
how bright it wanted to be. Within thirty years most large British towns had gas
mains. The lamplighter — a person who walked the route at dusk with a pole —
became one of the most familiar figures of the period, and one of the first
occupations destroyed by automation, when clockwork timers arrived.

Electricity arrived unevenly. The arc lamp came first and was, frankly, awful
for streets: extremely bright, harsh, and in need of daily maintenance because
the carbon electrodes burned away. Cities that installed arc lamps on tall
structures — the so-called moonlight towers, of which Austin, Texas retains a
few — were trying to light whole districts from a single point. The approach
failed everywhere it was tried, for a reason worth remembering: light falls off
with the square of distance, so lighting a large area from one high point wastes
most of the output.

The sodium lamp dominated the second half of the twentieth century, and it is
the reason older photographs of cities look orange. Low-pressure sodium is
monochromatic — it emits at essentially a single wavelength — which makes it
extraordinarily efficient and also makes colour vision impossible under it. A
red car and a brown car are the same car under sodium light. Traffic engineers
accepted that trade because the efficiency was unmatched.

LEDs have reversed the trade, and created a new problem. They are efficient and
they render colour well. But early municipal installations chose a very cool
white — around 4000 kelvin, sometimes higher — because it looks bright and
modern. The result was a wave of complaints about glare and sleep disruption,
and a body of research linking short-wavelength light at night to suppression of
melatonin. Several cities have since retrofitted warmer LEDs at 2700 kelvin.

The current frontier is not brightness at all but control. Because an LED can be
dimmed instantly and individually, a street can be dark until something moves.
Trials in the Netherlands have cut energy use by more than half while, contrary
to expectation, leaving reported feelings of safety unchanged. That last finding
is the one I want you to sit with. We have spent four centuries assuming that
more light means more safety, and the evidence for that proposition is far
thinner than the size of the investment would suggest.
"""

IELTS_LISTENING_4_QUESTIONS = [
    blank(
        "Early Paris lighting: householders had to hang a [[lantern]] outside",
        max_words=1,
    ),
    blank(
        "The original motivation for street lighting laws was [[crime]]",
        max_words=1,
    ),
    blank("First public gas-lit street: [[Pall Mall]], London", max_words=2),
    blank("Year the first gas street was lit: [[1807]]", max_words=1),
    blank(
        "The lamplighter's job was ended by clockwork [[timers|timer]]",
        max_words=1,
    ),
    blank(
        "Arc lamps needed daily maintenance because the [[carbon]] electrodes "
        "burned away",
        max_words=1,
    ),
    blank("Tall arc-lamp structures were known as [[moonlight towers]]", max_words=2),
    blank(
        "Low-pressure sodium light is [[monochromatic]], so colours cannot be "
        "distinguished",
        max_words=1,
    ),
    blank(
        "Cool white LEDs were criticised for glare and for suppressing "
        "[[melatonin]]",
        max_words=1,
    ),
    blank(
        "Dutch dimming trials cut energy use by over [[half|50%]] without "
        "changing reported safety",
        max_words=1,
    ),
]

# ============================================================ IELTS READING
IELTS_READING_1 = """\
THE ACCIDENTAL COLOUR

In the spring of 1856 an eighteen-year-old chemistry student named William Henry
Perkin was trying, and failing, to make quinine. Malaria was the great imperial
disease, quinine the only treatment, and the bark it came from was expensive and
politically awkward to obtain. Perkin's professor, August Wilhelm von Hofmann,
had speculated in print that quinine might be synthesised from coal tar, a waste
product of the gas industry that London produced in embarrassing quantity.

The speculation was, as Perkin later admitted, chemically naive. Hofmann had
matched the molecular formula of quinine against that of a coal-tar derivative
and concluded that one might be coaxed into the other. Molecular formulae say
nothing about how atoms are arranged, and the arrangement is the whole
difficulty. Perkin, working in a makeshift laboratory at the top of his father's
house during the Easter holiday, produced a reddish-brown sludge.

He was about to wash the flask out when he noticed that the alcohol he was using
turned an intense purple. This was the moment, though it did not look like one.
Purple was, at that date, the most expensive colour in the world. The classical
dye, Tyrian purple, was extracted from a Mediterranean sea snail at a rate that
required some twelve thousand molluscs to dye a single garment trim. The colour
had been the legal preserve of emperors for a reason: nobody else could afford
it.

What Perkin had made — he named it mauveine, and the shade came to be called
mauve — was the first synthetic dye of commercial significance. Crucially, it
was fast: it did not wash out or fade quickly in sunlight, which is where most
promising dyes had failed. He tested it on silk, sent samples to a dyeworks in
Perth, and received in return a letter of the kind that changes a life. If the
colour held on cotton as well as it held on silk, the dyer wrote, this would be
one of the most valuable discoveries in a very long time.

Perkin left college, over Hofmann's objections, and persuaded his father to
invest the family savings in a factory at Greenford Green. He was nineteen. The
enterprise required him to solve problems that had nothing to do with chemistry:
he had to manufacture his own raw materials, since nobody made them at scale,
and he had to invent the plant to do it. The first year was spent building
equipment rather than dye.

Fashion did the rest, and fashion is not a reliable partner. Mauve became
extravagantly popular after the Empress Eugénie of France was seen wearing it,
and Queen Victoria wore it to her daughter's wedding in 1858. Punch magazine
mocked the resulting epidemic as "the mauve measles". By 1863 the fashion had
passed, as fashions do, and Perkin's fortune would have passed with it had he
not already understood the more important point: the method mattered more than
the colour.

Coal tar, it turned out, contained the feedstock for a whole spectrum. Within
two decades German firms — Hoechst, BASF, Bayer, all founded on dye chemistry —
had industrialised the field and largely displaced British production. The same
laboratories, and the same techniques, produced the first synthetic drugs.
Aspirin was a dye company's product. So, later, were the sulphonamides, the
first effective antibacterials.

Perkin sold his works in 1874, at thirty-six, and spent the rest of his life on
pure research. The judgement usually passed on him is that he was lucky, and he
was. But the luck was narrow and the response to it was not: a great many
chemists had produced coloured sludge, and only one washed the flask out with
alcohol, noticed, and stopped.
"""

IELTS_READING_1_QUESTIONS = [
    tfng("Perkin was attempting to synthesise quinine when he made mauveine.", "T"),
    tfng("Hofmann's suggestion about coal tar was chemically sound.", "F"),
    tfng("Perkin carried out the experiment in his university laboratory.", "F"),
    tfng("Perkin's father refused to invest in the dye factory.", "F"),
    tfng("Tyrian purple was cheap to produce in large quantities.", "F"),
    tfng("Perkin patented mauveine before contacting the Perth dyeworks.", "NG"),
    tfng("The fashion for mauve had declined by the mid-1860s.", "T"),
    blank("Coal tar was a waste product of the [[gas]] industry.", max_words=1),
    blank(
        "Perkin's dye was commercially viable partly because it was [[fast]], "
        "resisting washing and sunlight.",
        max_words=1,
    ),
    blank("Perkin built his factory at [[Greenford Green]].", max_words=2),
    blank(
        "Punch magazine called the fashion for the colour the [[mauve measles]].",
        max_words=2,
    ),
    blank(
        "German companies founded on dye chemistry later produced the first "
        "synthetic [[drugs|medicines]].",
        max_words=1,
    ),
    blank(
        "Perkin sold his works in [[1874]] and returned to pure research.",
        max_words=1,
    ),
]

IELTS_READING_2 = """\
WHY CITIES ARE WARMER

Anyone who has walked out of a park into a street on a summer evening has felt
the urban heat island, even without a name for it. A large city can run several
degrees warmer than the countryside around it, and the difference is greatest
not at noon but a few hours after sunset — a detail that turns out to explain
most of the phenomenon.

The effect was first described systematically by Luke Howard, an amateur
meteorologist better known for naming cloud types, who published temperature
records for London in 1818. Howard noticed that the city was consistently warmer
than the surrounding fields, and that the gap was largest on still, clear
nights. He attributed it to the burning of fuel. He was partly right, and the
part he was wrong about is more interesting.

The dominant mechanism is thermal mass. Stone, brick, asphalt and concrete
absorb solar radiation during the day and release it slowly at night. Vegetation
and soil, by contrast, spend a large fraction of incoming energy on evaporating
water — a process that moves heat without raising temperature. A tree is, in
thermodynamic terms, an evaporative cooler running on sunlight. Removing the
trees and paving the ground does not merely remove shade; it removes the entire
evaporative pathway, so more of the day's energy ends up as sensible heat, and
that heat is stored in materials which then radiate through the night.

Geometry compounds this. A street lined with tall buildings forms what
climatologists call an urban canyon. Radiation that would escape upward from
flat ground instead bounces between facades, and each bounce is another chance
for absorption. The narrower and deeper the canyon, the more effectively it
traps energy. The same geometry reduces wind speed at street level, removing the
mixing that would otherwise carry warm air away. This is why the effect peaks on
still nights: wind is the city's only efficient means of losing heat, and tall
buildings are very good at stopping it.

Waste heat — Howard's explanation — is real but secondary in most cities.
Vehicles, industry, and above all air conditioning discharge energy directly
into the street. Air conditioning creates a feedback that ought to trouble urban
planners more than it does: the hotter the city becomes, the more air
conditioning is used, and the more heat is pumped outdoors, which makes the city
hotter. In dense districts of Tokyo and Phoenix, the contribution of waste heat
on extreme days is measurable in whole degrees.

The consequences are unevenly distributed, and that is the part that has moved
the subject from meteorology into public health. Heat maps of almost any large
city correlate closely with income. Wealthier districts have more trees, larger
gardens and lower building density; poorer districts have more asphalt, less
canopy and older housing stock with poorer insulation. During the 2003 European
heatwave, mortality was concentrated in exactly the districts a canopy map would
have predicted.

Remedies are well understood and unevenly applied. Reflective roofing — simply
painting a roof white — can lower internal temperature substantially at trivial
cost, though it works less well in cities with long cold winters, where the same
reflectivity is a heating penalty. Street trees are the most effective
intervention per unit spent, but they take fifteen to twenty years to deliver
their full benefit, which sits awkwardly with electoral cycles. Permeable paving
and restored waterways return the evaporative pathway that development removed.

None of this is technically difficult. The obstacle is that the cost is paid
now, locally and visibly, while the benefit arrives later, diffusely, and mostly
to people who are not yet living there.
"""

IELTS_READING_2_QUESTIONS = [
    mcq(
        "When is the urban heat island effect at its strongest?",
        ["At midday", "A few hours after sunset", "Just before dawn"],
        1,
    ),
    mcq(
        "What was Luke Howard better known for?",
        ["Naming cloud types", "Inventing the barometer", "Mapping London"],
        0,
    ),
    mcq(
        "According to the passage, what is the dominant cause of the effect?",
        [
            "Waste heat from vehicles and industry",
            "The thermal mass of urban materials",
            "Air pollution trapping radiation",
        ],
        1,
    ),
    mcq(
        "Why does vegetation keep an area cooler?",
        [
            "It reflects most incoming radiation",
            "It spends energy evaporating water",
            "It has a lower thermal mass than concrete",
        ],
        1,
    ),
    mcq(
        "What effect does an urban canyon have on radiation?",
        [
            "It reflects it away from the city",
            "It traps it through repeated absorption between facades",
            "It converts it into wind energy",
        ],
        1,
    ),
    mcq(
        "Why does the effect peak on still nights?",
        [
            "Wind is the city's main way of losing heat",
            "Humidity is higher when the air is still",
            "Traffic volumes are lower",
        ],
        0,
    ),
    mcq(
        "What does the passage say about air conditioning?",
        [
            "It creates a self-reinforcing feedback loop",
            "Its contribution is negligible everywhere",
            "It has been banned in some districts of Tokyo",
        ],
        0,
    ),
    ynng(
        "The writer believes the health effects of urban heat fall equally "
        "across income groups.",
        "N",
    ),
    ynng(
        "The writer considers street trees the most cost-effective single "
        "remedy.",
        "Y",
    ),
    ynng(
        "The writer argues that reflective roofing is suitable for every "
        "climate.",
        "N",
    ),
    ynng(
        "The writer states that permeable paving costs more than conventional "
        "paving.",
        "NG",
    ),
    blank(
        "Howard attributed the warmth of London to the burning of [[fuel]].",
        max_words=1,
    ),
    blank(
        "Street trees take fifteen to twenty [[years]] to deliver their full "
        "benefit.",
        max_words=1,
    ),
]

IELTS_READING_3 = """\
THE MEASUREMENT OF TIME AT SEA

The problem of longitude was, for two centuries, the most consequential unsolved
problem in Europe. Latitude — how far north or south a ship lies — can be read
from the sun at noon or the pole star at night with a simple instrument and a
table. Longitude has no such natural marker. The earth turns, and every point on
a given parallel looks identical to the sky.

The solution was understood in principle long before it was achieved in
practice. Because the earth rotates once in twenty-four hours, every hour of
difference between local time and the time at a reference meridian corresponds
to fifteen degrees of longitude. A navigator who knows the local time, which is
easy, and simultaneously knows the time at home, which is not, can calculate
position by subtraction. The difficulty was the second clock. No mechanism of
the seventeenth century could keep accurate time on a moving, damp,
temperature-swinging ship. Pendulums, the most accurate timekeepers on land, are
useless at sea for the obvious reason.

Two schools of thought competed, and their competition was less scientific than
it was social. The astronomers proposed the lunar distance method: the moon
moves against the background of fixed stars at a rate that is predictable, so
the moon is itself a clock, provided one has tables good enough and can measure
the angle precisely. The mechanists proposed building a better clock. The
astronomers had prestige, institutional backing and the Royal Observatory. The
mechanists had, eventually, John Harrison.

Harrison was a Lincolnshire carpenter with no formal training who spent forty
years on the problem. His insight was that the enemies of accuracy at sea were
friction, lubrication and thermal expansion, and that each could be engineered
away rather than tolerated. He built movements from lignum vitae, a wood so oily
that it lubricates itself, eliminating oil that would thicken in cold. He
invented the bimetallic strip, pairing two metals with different expansion rates
so that the effects cancelled — a device still found in thermostats today. He
replaced the pendulum with linked, counter-oscillating balances, so that a lurch
which sped one slowed the other.

His first three machines were large, brilliant and inconvenient. The fourth,
completed in 1759, was a departure: instead of scaling up, Harrison scaled down,
producing something resembling an oversized pocket watch. On a voyage to Jamaica
in 1761 it lost slightly over five seconds in eighty-one days, an accuracy that
exceeded the terms of the Longitude Act by a wide margin.

The Board of Longitude did not pay. What followed was a decade of shifted
criteria, demanded repetitions and required disclosures, presided over by Nevil
Maskelyne, the Astronomer Royal — who was, not incidentally, the principal
advocate of the competing lunar method and the publisher of the tables it
depended on. Historians still disagree about how far Maskelyne acted from
conviction and how far from interest. What is not in dispute is that Harrison
received the balance of his money only in 1773, at the age of eighty, and by
direct intervention of the king rather than by decision of the board.

The postscript complicates the moral. The lunar distance method was not
worthless; it was the only method available to most navigators for another fifty
years, because chronometers remained expensive and required skilled maintenance.
The two approaches were used together well into the nineteenth century, each
checking the other. Harrison won the argument, but not quickly, and not alone.
"""

IELTS_READING_3_QUESTIONS = [
    mcq(
        "Why is latitude easier to determine than longitude?",
        [
            "It can be read from the sun or pole star with simple instruments",
            "It does not change as a ship moves",
            "It requires no instruments at all",
        ],
        0,
    ),
    mcq(
        "One hour of time difference corresponds to how many degrees of longitude?",
        ["Ten", "Fifteen", "Twenty-four"],
        1,
    ),
    mcq(
        "Why were pendulum clocks unsuitable at sea?",
        [
            "A moving ship disturbs the pendulum",
            "They were too expensive to install",
            "They could not be read at night",
        ],
        0,
    ),
    mcq(
        "What did the lunar distance method use as a clock?",
        [
            "The moon's motion against the fixed stars",
            "The tides",
            "The sun's altitude at noon",
        ],
        0,
    ),
    mcq(
        "What was Harrison's occupation before he worked on timekeepers?",
        ["Carpenter", "Astronomer", "Naval officer"],
        0,
    ),
    mcq(
        "Why did Harrison use lignum vitae in his movements?",
        [
            "The wood is self-lubricating",
            "It is lighter than metal",
            "It resists salt water",
        ],
        0,
    ),
    mcq(
        "What problem did the bimetallic strip solve?",
        ["Thermal expansion", "Friction in the gear train", "Loss of power when winding"],
        0,
    ),
    mcq(
        "How did the fourth timekeeper differ from the first three?",
        ["It was much smaller", "It used a pendulum", "It was made entirely of wood"],
        0,
    ),
    tfng(
        "Harrison's fourth machine lost less than a minute on the 1761 voyage to "
        "Jamaica.",
        "T",
    ),
    tfng(
        "Nevil Maskelyne supported the mechanical solution to the longitude "
        "problem.",
        "F",
    ),
    tfng(
        "Harrison received the remainder of his award through the king's "
        "intervention.",
        "T",
    ),
    tfng(
        "Harrison's fourth timekeeper was reproduced in large numbers within five "
        "years.",
        "NG",
    ),
    tfng(
        "The lunar distance method fell out of use immediately after Harrison's "
        "success.",
        "F",
    ),
    blank(
        "Harrison replaced the pendulum with linked, counter-oscillating "
        "[[balances|balance]].",
        max_words=1,
    ),
]

# ============================================================ IELTS WRITING
IELTS_WRITING_TASK_1 = """\
WRITING TASK 1

You should spend about 20 minutes on this task.

The table below shows the percentage of households in four countries that
recycled glass, paper and plastic waste in 2005 and in 2025.

                 GLASS            PAPER           PLASTIC
             2005   2025      2005   2025     2005   2025
Germany       78%    92%       81%    94%      44%    71%
Japan         62%    88%       74%    91%      38%    82%
Brazil        21%    54%       33%    61%      12%    47%
Egypt          9%    31%       14%    38%       6%    22%

Summarise the information by selecting and reporting the main features, and make
comparisons where relevant.

Write at least 150 words.
"""

IELTS_WRITING_TASK_2 = """\
WRITING TASK 2

You should spend about 40 minutes on this task.

Write about the following topic:

    In many countries, the proportion of people working from home has risen
    sharply, and some employers now expect to close their offices entirely.

    Do the advantages of this development outweigh the disadvantages?

Give reasons for your answer and include any relevant examples from your own
knowledge or experience.

Write at least 250 words.
"""

# ============================================================ IELTS SPEAKING
IELTS_SPEAKING_PART_1 = """\
SPEAKING PART 1 — INTRODUCTION AND INTERVIEW (4-5 minutes)

In the real test the examiner checks your identity and then asks questions about
familiar topics. Answer each question in two or three sentences — Part 1 rewards
fluency and natural detail, not long speeches.

Record one continuous answer covering all of the following.

Let's talk about where you live.
  * Do you live in a house or an apartment?
  * What do you like most about the area you live in?
  * Would you like to move somewhere else in the future? Why?

Now let's move on to talk about reading.
  * How often do you read for pleasure?
  * Did you enjoy reading when you were a child?
  * Do you think people read more or less than they used to? Why?
"""

IELTS_SPEAKING_PART_2 = """\
SPEAKING PART 2 — LONG TURN (1 minute preparation, 1-2 minutes speaking)

You have one minute to prepare, and you may make notes. Then speak for between
one and two minutes. Recording stops automatically at two minutes.

  Describe a decision you made that took a long time to make.

  You should say:
    * what the decision was
    * why it took you a long time
    * who, if anyone, you consulted

  and explain whether you think it was the right decision.
"""

IELTS_SPEAKING_PART_3 = """\
SPEAKING PART 3 — DISCUSSION (4-5 minutes)

The examiner asks broader questions connected to the Part 2 topic. Answers here
should be longer and more analytical: take a position, support it, and consider
an objection.

Record one continuous answer covering all of the following.
  * Why do you think some people find decisions harder to make than others?
  * Is it better to make decisions quickly or slowly? Does it depend on the kind
    of decision?
  * Some people say that having more choices makes people less happy. Do you
    agree?
  * How has technology changed the way people make important decisions?
  * Should young people be taught how to make decisions at school?
"""


# --- test definitions -------------------------------------------------------
IELTS_DESCRIPTION = (
    "A full-length IELTS Academic practice paper at the real exam's structure: "
    "Listening (four recordings, 40 questions, 30 minutes), Academic Reading "
    "(three passages, 40 questions, 60 minutes), Writing (Task 1 and Task 2 in "
    "one 60-minute block, with Task 2 worth twice Task 1) and Speaking (three "
    f"parts, 14 minutes). {NO_EXAMINER_NOTE} Scores are estimates: the "
    "raw-to-band tables are prep-industry approximations, not official."
)

CUSTOM_DESCRIPTION = (
    "A short in-house placement check, scored as a straight percentage rather "
    "than an exam band. It doubles as the worked example to copy when authoring "
    "your own test: duplicate it, swap the content, adjust the timings."
)

TESTS = [
    {
        "format": "ielts_academic",
        "title": "IELTS Academic — Full Practice Test 1",
        "difficulty": DifficultyLevel.INTERMEDIATE,
        "description": IELTS_DESCRIPTION,
        "sections": [
            {
                "skill": SectionSkill.LISTENING,
                "title": "Listening",
                # 30 minutes for the four recordings. The real paper-based exam
                # adds 10 minutes to copy answers onto an answer sheet; there is
                # no answer sheet here, so that time would simply be a gift.
                "duration": 30,
                # The recording plays once, continuously, and does not rewind.
                "item_flow": ItemFlow.SEQUENTIAL,
                "instructions": (
                    "Four recordings, ten questions each, taken in order. You "
                    f"cannot return to a part once you move on. {AUDIO_PENDING}"
                ),
                "exercises": [
                    {
                        "title": "IELTS Listening — Part 1: Student housing enquiry",
                        "type": ExerciseType.LISTENING,
                        "prompt": (
                            "Questions 1-10. Complete the form. Write NO MORE "
                            "THAN TWO WORDS AND/OR A NUMBER for each answer."
                        ),
                        "content": IELTS_LISTENING_1_TRANSCRIPT,
                        "questions": IELTS_LISTENING_1_QUESTIONS,
                    },
                    {
                        "title": "IELTS Listening — Part 2: Community sports centre",
                        "type": ExerciseType.LISTENING,
                        "prompt": (
                            "Questions 11-20. Choose the correct answer for "
                            "each question."
                        ),
                        "content": IELTS_LISTENING_2_TRANSCRIPT,
                        "questions": IELTS_LISTENING_2_QUESTIONS,
                    },
                    {
                        "title": "IELTS Listening — Part 3: Food waste project",
                        "type": ExerciseType.LISTENING,
                        "prompt": (
                            "Questions 21-30. Choose the correct answer for "
                            "each question."
                        ),
                        "content": IELTS_LISTENING_3_TRANSCRIPT,
                        "questions": IELTS_LISTENING_3_QUESTIONS,
                    },
                    {
                        "title": "IELTS Listening — Part 4: Street lighting",
                        "type": ExerciseType.LISTENING,
                        "prompt": (
                            "Questions 31-40. Complete the notes. Write NO MORE "
                            "THAN TWO WORDS AND/OR A NUMBER for each answer."
                        ),
                        "content": IELTS_LISTENING_4_TRANSCRIPT,
                        "questions": IELTS_LISTENING_4_QUESTIONS,
                    },
                ],
            },
            {
                "skill": SectionSkill.READING,
                "title": "Academic Reading",
                "duration": 60,
                # All three passages at once — that is the real paper.
                "item_flow": ItemFlow.FREE,
                "instructions": (
                    "Three passages, 40 questions, 60 minutes. All three are "
                    "available now; budget roughly 20 minutes each. No extra "
                    "transfer time is given."
                ),
                "exercises": [
                    {
                        "title": "IELTS Reading — Passage 1: The accidental colour",
                        "type": ExerciseType.READING,
                        "prompt": (
                            "Questions 1-13. Answer TRUE / FALSE / NOT GIVEN, "
                            "then complete the sentences using NO MORE THAN TWO "
                            "WORDS from the passage."
                        ),
                        "content": IELTS_READING_1,
                        "questions": IELTS_READING_1_QUESTIONS,
                    },
                    {
                        "title": "IELTS Reading — Passage 2: Why cities are warmer",
                        "type": ExerciseType.READING,
                        "prompt": (
                            "Questions 14-26. Choose the correct answer, decide "
                            "whether each claim reflects the writer's views "
                            "(YES / NO / NOT GIVEN), then complete the sentences."
                        ),
                        "content": IELTS_READING_2,
                        "questions": IELTS_READING_2_QUESTIONS,
                    },
                    {
                        "title": (
                            "IELTS Reading — Passage 3: The measurement of time at sea"
                        ),
                        "type": ExerciseType.READING,
                        "prompt": (
                            "Questions 27-40. Choose the correct answer, then "
                            "answer TRUE / FALSE / NOT GIVEN and complete the "
                            "final sentence."
                        ),
                        "content": IELTS_READING_3,
                        "questions": IELTS_READING_3_QUESTIONS,
                    },
                ],
            },
            {
                "skill": SectionSkill.WRITING,
                "title": "Writing",
                # One 60-minute block for both tasks. The 20/40 split is the
                # exam's *advice*, not a rule it enforces — so neither do we.
                "duration": 60,
                "item_flow": ItemFlow.FREE,
                "instructions": (
                    "Two tasks, 60 minutes in total. Spend about 20 minutes on "
                    "Task 1 and about 40 on Task 2 — Task 2 is worth twice as "
                    "much and is marked accordingly. You may work on either task "
                    "at any point in the hour."
                ),
                "exercises": [
                    {
                        "title": "IELTS Writing — Task 1: Recycling rates",
                        "type": ExerciseType.WRITING,
                        "prompt": IELTS_WRITING_TASK_1,
                        "content": None,
                        "questions": [],
                        "weight": 1,
                    },
                    {
                        "title": "IELTS Writing — Task 2: Working from home",
                        "type": ExerciseType.WRITING,
                        "prompt": IELTS_WRITING_TASK_2,
                        "content": None,
                        "questions": [],
                        # The whole reason TestSectionExercise.weight exists.
                        "weight": 2,
                    },
                ],
            },
            {
                "skill": SectionSkill.SPEAKING,
                "title": "Speaking",
                "duration": 14,
                # An interview does not let you answer Part 3 before Part 1.
                "item_flow": ItemFlow.SEQUENTIAL,
                "instructions": (
                    "Three parts, taken in order, 11-14 minutes in total. "
                    f"{NO_EXAMINER_NOTE}"
                ),
                "exercises": [
                    {
                        "title": "IELTS Speaking — Part 1: Introduction and interview",
                        "type": ExerciseType.SPEAKING,
                        "prompt": IELTS_SPEAKING_PART_1,
                        "content": None,
                        "questions": [],
                        "max_record_seconds": 300,
                    },
                    {
                        "title": "IELTS Speaking — Part 2: Long turn",
                        "type": ExerciseType.SPEAKING,
                        "prompt": IELTS_SPEAKING_PART_2,
                        "content": None,
                        "questions": [],
                        "prep_seconds": 60,
                        "max_record_seconds": 120,
                    },
                    {
                        "title": "IELTS Speaking — Part 3: Discussion",
                        "type": ExerciseType.SPEAKING,
                        "prompt": IELTS_SPEAKING_PART_3,
                        "content": None,
                        "questions": [],
                        "max_record_seconds": 300,
                    },
                ],
            },
        ],
    },
    {
        "format": "custom",
        "title": "Placement check — Reading and Writing",
        "difficulty": DifficultyLevel.BEGINNER,
        "description": CUSTOM_DESCRIPTION,
        "sections": [
            {
                "skill": SectionSkill.READING,
                "title": "Reading",
                "duration": 15,
                "item_flow": ItemFlow.FREE,
                "instructions": "One short passage, seven questions.",
                "exercises": [
                    {
                        "title": "Placement — Reading: Why cities are warmer",
                        "type": ExerciseType.READING,
                        "prompt": "Read the passage and answer the questions.",
                        "content": IELTS_READING_2,
                        "questions": IELTS_READING_2_QUESTIONS[:7],
                    },
                ],
            },
            {
                "skill": SectionSkill.WRITING,
                "title": "Writing",
                "duration": 20,
                "item_flow": ItemFlow.FREE,
                "instructions": "One short essay. Write at least 150 words.",
                "exercises": [
                    {
                        "title": "Placement — Writing: Working from home",
                        "type": ExerciseType.WRITING,
                        "prompt": IELTS_WRITING_TASK_2,
                        "content": None,
                        "questions": [],
                    },
                ],
            },
        ],
    },
]


class Command(BaseCommand):
    help = "Seed the mock-test library with ready-to-take practice tests."

    @transaction.atomic
    def handle(self, *args, **options):
        # Conversion tables must exist before a test can be scored.
        call_command("seed_test_formats")

        module, _ = LearningModule.objects.get_or_create(
            title=MODULE_TITLE,
            defaults={
                "description": (
                    "Passages, prompts and questions used by the mock-test "
                    "library. Managed by seed_mock_tests."
                ),
                "difficulty_level": DifficultyLevel.INTERMEDIATE,
            },
        )

        for spec in TESTS:
            fmt = TestFormat.objects.filter(slug=spec["format"]).first()
            if fmt is None:
                self.stderr.write(f"Format {spec['format']} missing — skipped")
                continue

            template, created = MockTestTemplate.objects.update_or_create(
                title=spec["title"],
                defaults={
                    "format": fmt,
                    "description": spec["description"],
                    "difficulty_level": spec["difficulty"],
                },
            )
            # Rebuilding sections would hit PROTECT once anyone has sat the
            # test — and would silently invalidate their score report.
            if not created and template.sections.exists():
                self.stdout.write(f"{template.title}: already stocked, left alone")
                continue

            for order, section_spec in enumerate(spec["sections"]):
                section = TestSection.objects.create(
                    template=template,
                    skill=section_spec["skill"],
                    title=section_spec["title"],
                    order=order,
                    duration_minutes=section_spec["duration"],
                    instructions=section_spec["instructions"],
                    item_flow=section_spec.get("item_flow", ItemFlow.FREE),
                )
                for item_order, ex_spec in enumerate(section_spec["exercises"]):
                    TestSectionExercise.objects.create(
                        section=section,
                        exercise=self._exercise(module, ex_spec),
                        order=item_order,
                        weight=ex_spec.get("weight", 1),
                        prep_seconds=ex_spec.get("prep_seconds"),
                        max_record_seconds=ex_spec.get("max_record_seconds"),
                    )

            question_count = sum(
                len(ex["questions"])
                for sec in spec["sections"] for ex in sec["exercises"]
            )
            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if created else 'Restocked'} {template.title} "
                f"({len(spec['sections'])} sections, {question_count} questions)"
            ))

    def _exercise(self, module, spec):
        """Create (or refresh) one exercise and its question set."""
        exercise, _ = Exercise.objects.update_or_create(
            title=spec["title"],
            defaults={
                "module": module,
                "exercise_type": spec["type"],
                "prompt_text": spec["prompt"],
                "content_text": spec["content"],
            },
        )
        exercise.questions.all().delete()
        for order, q_spec in enumerate(spec["questions"]):
            question = Question.objects.create(
                exercise=exercise,
                text=q_spec["text"],
                order=order,
                max_words=q_spec.get("max_words"),
            )
            for opt_order, option_text in enumerate(q_spec["options"]):
                QuestionOption.objects.create(
                    question=question,
                    text=option_text,
                    is_correct=(opt_order == q_spec["correct"]),
                    order=opt_order,
                )
        return exercise
