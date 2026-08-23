"""Seed rubric templates + criteria + band descriptors (idempotent).

    python manage.py seed_rubrics

docs/research/02-scoring-rubrics.md §4.6. Keyed on RubricTemplate.slug /
criterion code / band_value via update_or_create, so it is safe to re-run on
every deploy (matching seed_test_formats.py practice) and is invoked at the end
of seed_demo.

Descriptor texts are faithful short summaries of the PUBLIC band descriptors
recorded in each template's `description` (public versions are distributable for
exactly this purpose — see research doc §3.1). The IELTS task-level aggregation
(mean rounded down to nearest 0.5) is examiner convention, not officially
published, so it lives in the `aggregation` data field and can be flipped by a
data edit — never a migration.
"""

from django.core.management.base import BaseCommand

from core.models import (
    ExerciseType,
    RubricAggregation,
    RubricBandDescriptor,
    RubricCriterion,
    RubricTemplate,
)

IELTS_WRITING_SRC = (
    "IELTS Writing Task 2 band descriptors (public version, updated May 2023): "
    "https://takeielts.britishcouncil.org/sites/default/files/"
    "ielts_writing_band_descriptors.pdf"
)
IELTS_SPEAKING_SRC = (
    "IELTS Speaking band descriptors (public version): "
    "https://idc.edu/IELTS-Speaking-Writing-Band-descriptors.pdf"
)
TOEIC_SPEAKING_SRC = (
    "TOEIC Speaking proficiency levels (ETS score descriptors, 8 levels): "
    "https://www.ets.org/pdfs/toeic/toeic-speaking-writing-score-descriptors.pdf"
)
TOEIC_WRITING_SRC = (
    "TOEIC Writing proficiency levels (ETS score descriptors, 9 levels): "
    "https://www.ets.org/pdfs/toeic/toeic-speaking-writing-score-descriptors.pdf"
)
CEFR_SRC = (
    "CEFR Table 3 — qualitative aspects of spoken language use (Range, Accuracy, "
    "Fluency, Coherence; Interaction dropped): https://www.coe.int/en/web/"
    "common-european-framework-reference-languages/"
    "table-3-cefr-3.3-common-reference-levels-qualitative-aspects-of-spoken-language-use"
)


# Each template: criteria -> {band_value: (label, descriptor)}. Bands are a
# representative subset (teachers may still score any in-scale value); the cells
# supply the hover wording.
TEMPLATES = [
    {
        "slug": "ielts-writing-task2",
        "name": "IELTS Writing Task 2",
        "exercise_type": ExerciseType.WRITING,
        "is_default_for_type": True,
        "scale_min": 0, "scale_max": 9, "score_step": 1,
        "aggregation": RubricAggregation.MEAN_DOWN_HALF,
        "description": IELTS_WRITING_SRC,
        "criteria": [
            ("task_response", "Task Response", {
                9: ("Band 9", "Fully addresses all parts of the task with a fully developed position and well-supported, relevant ideas."),
                7: ("Band 7", "Addresses all parts of the task; presents a clear position throughout with extended, supported ideas."),
                5: ("Band 5", "Addresses the task only partially; position is expressed but development is limited or repetitive."),
                3: ("Band 3", "Does not adequately address the task; few relevant ideas, possibly off-topic."),
            }),
            ("coherence_cohesion", "Coherence & Cohesion", {
                9: ("Band 9", "Cohesion is used so skilfully it attracts no attention; paragraphing is fully appropriate."),
                7: ("Band 7", "Logically organises information with clear progression; uses cohesive devices appropriately."),
                5: ("Band 5", "Some organisation, but overall progression is not always logical; cohesion is faulty or mechanical."),
                3: ("Band 3", "No apparent logical organisation; relationships between ideas are unclear."),
            }),
            ("lexical_resource", "Lexical Resource", {
                9: ("Band 9", "Wide range of vocabulary used naturally and precisely; very rare minor errors."),
                7: ("Band 7", "Sufficient range to allow flexibility and precision; some less common items with occasional errors."),
                5: ("Band 5", "Limited range adequate for the task; noticeable errors in word choice, spelling or word formation."),
                3: ("Band 3", "Very limited vocabulary; errors severely distort meaning."),
            }),
            ("grammatical_range", "Grammatical Range & Accuracy", {
                9: ("Band 9", "Full range of structures used with full flexibility and accuracy; rare minor errors."),
                7: ("Band 7", "Variety of complex structures; frequent error-free sentences with good control."),
                5: ("Band 5", "Limited range of structures; attempts complex sentences but with frequent errors."),
                3: ("Band 3", "Attempts sentence forms but errors predominate and distort meaning."),
            }),
        ],
    },
    {
        "slug": "ielts-speaking",
        "name": "IELTS Speaking",
        "exercise_type": ExerciseType.SPEAKING,
        "is_default_for_type": True,
        "scale_min": 0, "scale_max": 9, "score_step": 1,
        "aggregation": RubricAggregation.MEAN_DOWN_HALF,
        "description": IELTS_SPEAKING_SRC,
        "criteria": [
            ("fluency_coherence", "Fluency & Coherence", {
                9: ("Band 9", "Speaks fluently with only rare repetition or self-correction; develops topics coherently and appropriately."),
                7: ("Band 7", "Speaks at length without noticeable effort; some hesitation is content- not language-related."),
                5: ("Band 5", "Usually maintains flow but uses repetition and self-correction; may over-use connectives."),
                3: ("Band 3", "Speaks with long pauses; limited ability to link simple sentences."),
            }),
            ("lexical_resource", "Lexical Resource", {
                9: ("Band 9", "Uses vocabulary with full flexibility and precision; idiomatic language used naturally."),
                7: ("Band 7", "Uses vocabulary flexibly to discuss a variety of topics; some less common and idiomatic items."),
                5: ("Band 5", "Manages to talk about familiar and unfamiliar topics but uses vocabulary with limited flexibility."),
                3: ("Band 3", "Uses simple vocabulary to convey personal information; insufficient for less familiar topics."),
            }),
            ("grammatical_range", "Grammatical Range & Accuracy", {
                9: ("Band 9", "Uses a full range of structures naturally and appropriately; consistently accurate."),
                7: ("Band 7", "Uses a range of complex structures with some flexibility; frequent error-free sentences."),
                5: ("Band 5", "Produces basic sentence forms with reasonable accuracy; limited range of complex structures."),
                3: ("Band 3", "Attempts basic forms but with limited success; frequent errors."),
            }),
            ("pronunciation", "Pronunciation", {
                9: ("Band 9", "Uses a full range of pronunciation features with precision; effortless to understand."),
                7: ("Band 7", "Shows all the positive features of band 6 and some of band 8; generally easy to understand."),
                5: ("Band 5", "Shows some effective use of features but not sustained; can usually be understood."),
                3: ("Band 3", "Shows some features of band 2 and some of band 4; frequent lapses strain the listener."),
            }),
        ],
    },
    {
        "slug": "toeic-writing-proficiency",
        "name": "TOEIC Writing Proficiency",
        "exercise_type": ExerciseType.WRITING,
        "is_default_for_type": False,
        "scale_min": 1, "scale_max": 9, "score_step": 1,
        "aggregation": RubricAggregation.MEAN,
        "description": TOEIC_WRITING_SRC,
        "criteria": [
            ("overall_proficiency", "Overall Proficiency", {
                9: ("Level 9 (200)", "Consistently creates well-organised, well-developed text with reasons, examples and details; strong control of grammar and vocabulary."),
                8: ("Level 8 (170-190)", "Generally well-organised and developed text; good facility with language, with occasional lapses."),
                7: ("Level 7 (150-160)", "Adequately organised text with relevant support; some errors that do not obscure meaning."),
                6: ("Level 6 (130-140)", "Some organisation but development may be uneven; noticeable grammar/vocabulary limitations."),
                5: ("Level 5 (110-120)", "Limited organisation and development; errors sometimes interfere with meaning."),
                4: ("Level 4 (90-100)", "Minimal development; frequent errors that interfere with communication."),
                3: ("Level 3 (70-80)", "Very limited ability to connect ideas; serious and frequent errors."),
                2: ("Level 2 (50-60)", "Little evidence of ability to express ideas in writing."),
                1: ("Level 1 (0-40)", "No effective written communication."),
            }),
        ],
    },
    {
        "slug": "toeic-speaking-proficiency",
        "name": "TOEIC Speaking Proficiency",
        "exercise_type": ExerciseType.SPEAKING,
        "is_default_for_type": False,
        "scale_min": 1, "scale_max": 8, "score_step": 1,
        "aggregation": RubricAggregation.MEAN,
        "description": TOEIC_SPEAKING_SRC,
        "criteria": [
            ("overall_proficiency", "Overall Proficiency", {
                8: ("Level 8 (190-200)", "Communicates effectively with native-like fluency; sustained, coherent, well-developed responses."),
                7: ("Level 7 (160-180)", "Generally effective communication; minor lapses in pronunciation, intonation or grammar."),
                6: ("Level 6 (130-150)", "Relevant responses, generally intelligible; some limitations in grammar and vocabulary."),
                5: ("Level 5 (110-120)", "Adequate for routine tasks; noticeable pronunciation or grammar issues at times obscure meaning."),
                4: ("Level 4 (80-100)", "Limited but functional; responses are short with frequent errors and hesitation."),
                3: ("Level 3 (60-70)", "Very limited; can produce isolated words or memorised phrases."),
                2: ("Level 2 (40-50)", "Minimal spoken production; difficult to understand."),
                1: ("Level 1 (0-30)", "No effective spoken communication."),
            }),
        ],
    },
    {
        "slug": "cefr-general",
        "name": "CEFR General (Spoken)",
        "exercise_type": ExerciseType.SPEAKING,
        "is_default_for_type": False,
        "scale_min": 1, "scale_max": 6, "score_step": 1,
        "aggregation": RubricAggregation.MEAN_NEAREST_HALF,
        "description": CEFR_SRC,
        "criteria": [
            ("range", "Range", {
                6: ("C2", "Great flexibility reformulating ideas; conveys finer shades of meaning precisely."),
                5: ("C1", "Broad range of language allowing clear, well-structured expression without obvious searching."),
                4: ("B2", "Sufficient range to give clear descriptions and express viewpoints on most general topics."),
                3: ("B1", "Enough language to describe unpredictable situations and express thought on abstract/cultural topics."),
                2: ("A2", "Basic phrases and formulae for simple everyday needs and predictable situations."),
                1: ("A1", "Very basic repertoire of words and simple phrases about personal details."),
            }),
            ("accuracy", "Accuracy", {
                6: ("C2", "Consistent grammatical control of complex language, even while attention is elsewhere."),
                5: ("C1", "Consistently high degree of grammatical accuracy; errors are rare."),
                4: ("B2", "Good grammatical control; occasional slips but no errors causing misunderstanding."),
                3: ("B1", "Reasonably accurate in familiar contexts; noticeable mother-tongue influence."),
                2: ("A2", "Uses simple structures correctly but still systematically makes basic mistakes."),
                1: ("A1", "Only limited control of a few simple grammatical structures and sentence patterns."),
            }),
            ("fluency", "Fluency", {
                6: ("C2", "Expresses effortlessly and naturally; only a conceptually difficult subject hinders flow."),
                5: ("C1", "Expresses fluently and spontaneously with little obvious searching for expressions."),
                4: ("B2", "Produces stretches of language at a fairly even tempo; few noticeably long pauses."),
                3: ("B1", "Keeps going comprehensibly though pausing for grammar and lexis is evident in longer stretches."),
                2: ("A2", "Makes self understood in short utterances though pauses and false starts are evident."),
                1: ("A1", "Manages very short, isolated, mainly pre-packaged utterances with much pausing."),
            }),
            ("coherence", "Coherence", {
                6: ("C2", "Creates coherent and cohesive discourse with full and appropriate use of organisational patterns."),
                5: ("C1", "Produces clear, smoothly flowing, well-structured speech with controlled use of connectors."),
                4: ("B2", "Uses a limited number of cohesive devices to link utterances into clear, connected discourse."),
                3: ("B1", "Links a series of shorter discrete elements into a connected, linear sequence of points."),
                2: ("A2", "Links groups of words with simple connectors like 'and', 'but' and 'because'."),
                1: ("A1", "Links words or groups of words with very basic linear connectors like 'and' or 'then'."),
            }),
        ],
    },
]


class Command(BaseCommand):
    help = "Seed rubric templates, criteria and band descriptors (idempotent)."

    def handle(self, *args, **options):
        for spec in TEMPLATES:
            template, created = RubricTemplate.objects.update_or_create(
                slug=spec["slug"],
                defaults={
                    "name": spec["name"],
                    "description": spec["description"],
                    "exercise_type": spec["exercise_type"],
                    "is_default_for_type": spec["is_default_for_type"],
                    "scale_min": spec["scale_min"],
                    "scale_max": spec["scale_max"],
                    "score_step": spec["score_step"],
                    "aggregation": spec["aggregation"],
                    "is_active": True,
                },
            )
            for order, (code, name, bands) in enumerate(spec["criteria"], start=1):
                criterion, _ = RubricCriterion.objects.update_or_create(
                    template=template, code=code,
                    defaults={"name": name, "order": order, "weight": 1},
                )
                for band_value, (label, descriptor) in bands.items():
                    RubricBandDescriptor.objects.update_or_create(
                        criterion=criterion, band_value=band_value,
                        defaults={"label": label, "descriptor": descriptor},
                    )
            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if created else 'Updated'} {template.slug} "
                f"({len(spec['criteria'])} criteria)"
            ))
