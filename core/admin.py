"""Django admin — the main reason for the migration.

Custom UserAdmin (the User model is a custom AbstractBaseUser) plus
registrations for every domain model with sensible list/filter/search config.
"""

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField

from .models import (
    Assignment,
    Class,
    ClassStudent,
    CriterionScore,
    Exercise,
    Feedback,
    LearningModule,
    LessonPlan,
    Meeting,
    MockTestTemplate,
    PronunciationAttempt,
    PronunciationDrill,
    Question,
    QuestionOption,
    RubricBandDescriptor,
    RubricCriterion,
    RubricTemplate,
    ScoreConversionTable,
    SectionAttempt,
    StudentModuleProgress,
    Submission,
    TestAttempt,
    TestFormat,
    TestSection,
    TestSectionExercise,
    User,
    WritingAnnotation,
    StudyMaterial,
    AiInsight,
    AiModel,
    SystemLog,
)


# ---------- User ----------
class UserCreationForm(forms.ModelForm):
    password = forms.CharField(label="Password", widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ("email", "full_name", "role", "status", "avatar_url")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user


class UserChangeForm(forms.ModelForm):
    password = ReadOnlyPasswordHashField(
        help_text="Raw passwords are not stored; use the change-password form."
    )

    class Meta:
        model = User
        fields = "__all__"

    def clean_password(self):
        return self.initial.get("password")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    list_display = ("id", "email", "full_name", "role", "status", "is_staff")
    list_filter = ("role", "status", "is_staff", "is_superuser")
    search_fields = ("email", "full_name")
    ordering = ("email",)
    filter_horizontal = ("groups", "user_permissions")
    readonly_fields = ("last_login", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("full_name", "avatar_url", "role", "status")}),
        ("Permissions", {"fields": (
            "is_active", "is_staff", "is_superuser", "groups", "user_permissions",
        )}),
        ("Timestamps", {"fields": ("last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": (
                "email", "full_name", "role", "status",
                "password", "is_staff", "is_superuser",
            ),
        }),
    )


# ---------- Classes ----------
class ClassStudentInline(admin.TabularInline):
    model = ClassStudent
    extra = 0
    raw_id_fields = ("student",)


@admin.register(Class)
class ClassAdmin(admin.ModelAdmin):
    list_display = ("id", "class_name", "teacher", "academic_year", "created_at")
    list_filter = ("academic_year",)
    search_fields = ("class_name",)
    raw_id_fields = ("teacher",)
    inlines = [ClassStudentInline]


@admin.register(ClassStudent)
class ClassStudentAdmin(admin.ModelAdmin):
    list_display = ("id", "klass", "student", "joined_at")
    raw_id_fields = ("klass", "student")


@admin.register(LessonPlan)
class LessonPlanAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "klass", "start_date", "end_date")
    search_fields = ("title",)
    raw_id_fields = ("klass",)


# ---------- Modules / Exercises ----------
class ExerciseInline(admin.TabularInline):
    model = Exercise
    extra = 0


@admin.register(LearningModule)
class LearningModuleAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "difficulty_level", "band", "topic", "created_by", "created_at")
    list_filter = ("difficulty_level", "band", "topic")
    search_fields = ("title",)
    raw_id_fields = ("created_by",)
    inlines = [ExerciseInline]


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0


@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "exercise_type", "band", "topic", "module", "created_by", "created_at")
    list_filter = ("exercise_type", "band", "topic")
    search_fields = ("title", "prompt_text", "content_text")
    raw_id_fields = ("module", "created_by")
    inlines = [QuestionInline]


class QuestionOptionInline(admin.TabularInline):
    model = QuestionOption
    extra = 0


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("id", "exercise", "order", "max_words", "text")
    list_filter = ("exercise__exercise_type",)
    search_fields = ("text",)
    raw_id_fields = ("exercise",)
    inlines = [QuestionOptionInline]


@admin.register(StudentModuleProgress)
class StudentModuleProgressAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "module", "completion_percentage", "last_accessed_at")
    raw_id_fields = ("student", "module")


# ---------- Assignments / Submissions / Feedback ----------
@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ("id", "klass", "exercise", "assigned_by", "due_date", "created_at")
    raw_id_fields = ("klass", "exercise", "assigned_by")


class FeedbackInline(admin.StackedInline):
    model = Feedback
    extra = 0
    raw_id_fields = ("reviewer",)


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "exercise", "submission_type", "status", "auto_score", "submitted_at")
    list_filter = ("submission_type", "status")
    raw_id_fields = ("assignment", "exercise", "student")
    inlines = [FeedbackInline]


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ("id", "submission", "reviewer", "score", "is_ai_generated", "created_at")
    list_filter = ("is_ai_generated",)
    raw_id_fields = ("submission", "reviewer")


# ---------- Study Materials ----------
@admin.register(StudyMaterial)
class StudyMaterialAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "uploaded_by", "klass", "created_at")
    list_filter = ("created_at",)
    search_fields = ("title", "description", "file_url")
    raw_id_fields = ("uploaded_by", "klass")


# ---------- AI model registry ----------
@admin.register(AiModel)
class AiModelAdmin(admin.ModelAdmin):
    list_display = ("id", "model_name", "endpoint_url", "version_identifier", "is_active", "strictness", "updated_at")
    list_filter = ("is_active", "strictness")
    search_fields = ("model_name", "version_identifier")
    raw_id_fields = ("updated_by",)


# ---------- System Log ----------
@admin.register(SystemLog)
class SystemLogAdmin(admin.ModelAdmin):
    list_display = ("id", "admin", "action_description", "target_status", "timestamp")
    list_filter = ("target_status", "timestamp")
    search_fields = ("action_description",)
    raw_id_fields = ("admin",)


# ---------- Mock tests ----------
# Formats and conversion tables are admin-editable data by design: adding an
# exam flavour (TOEFL, an internal CEFR placement) is rows here, not a migration.
@admin.register(TestFormat)
class TestFormatAdmin(admin.ModelAdmin):
    list_display = ("id", "slug", "name", "version", "overall_strategy", "is_active")
    list_filter = ("overall_strategy", "is_active")
    search_fields = ("slug", "name")
    # Clearing `is_active` is how a format is retired: its rows and conversion
    # tables stay (old reports still convert through them) but it and its
    # templates leave the student-facing catalogue.
    actions = ["activate", "deactivate"]

    @admin.action(description="Activate selected formats")
    def activate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=True)} activated.")

    @admin.action(description="Retire selected formats (hide from catalogue)")
    def deactivate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=False)} retired.")


@admin.register(ScoreConversionTable)
class ScoreConversionTableAdmin(admin.ModelAdmin):
    list_display = ("id", "format", "skill", "source_note", "updated_at")
    list_filter = ("skill", "format")
    raw_id_fields = ("format",)


class TestSectionExerciseInline(admin.TabularInline):
    model = TestSectionExercise
    extra = 1
    raw_id_fields = ("exercise",)
    # `weight` is how IELTS Writing Task 2 counts double; the speaking timings
    # are per-part. All three are authoring data, so they belong on the inline.
    fields = (
        "exercise", "order", "weight", "prep_seconds", "max_record_seconds",
    )


class TestSectionInline(admin.TabularInline):
    model = TestSection
    extra = 1
    show_change_link = True   # items are edited on the section's own page


@admin.register(MockTestTemplate)
class MockTestTemplateAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "format", "difficulty_level", "created_at")
    list_filter = ("format", "difficulty_level")
    search_fields = ("title", "description")
    raw_id_fields = ("created_by",)
    inlines = [TestSectionInline]


@admin.register(TestSection)
class TestSectionAdmin(admin.ModelAdmin):
    list_display = (
        "id", "template", "order", "title", "skill",
        "duration_minutes", "item_flow",
    )
    list_filter = ("skill", "item_flow")
    raw_id_fields = ("template",)
    inlines = [TestSectionExerciseInline]


class SectionAttemptInline(admin.TabularInline):
    model = SectionAttempt
    extra = 0
    # Timing and scores are owned by core/mock_tests.py; the admin is for
    # inspection, so editing them here would only desync the state machine.
    readonly_fields = (
        "section", "status", "started_at", "expires_at", "completed_at",
        "raw_score", "raw_max", "converted_score", "draft_answers",
    )
    can_delete = False


@admin.register(TestAttempt)
class TestAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "id", "template", "student", "status", "mode",
        "overall_score", "started_at", "completed_at",
    )
    list_filter = ("status", "mode", "template__format")
    raw_id_fields = ("template", "student")
    inlines = [SectionAttemptInline]


# ---------- Scoring rubrics (docs/research/02-scoring-rubrics.md) ----------
class RubricCriterionInline(admin.TabularInline):
    model = RubricCriterion
    extra = 1
    show_change_link = True   # band descriptors are edited on the criterion page


class RubricBandDescriptorInline(admin.TabularInline):
    model = RubricBandDescriptor
    extra = 1


@admin.register(RubricTemplate)
class RubricTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "id", "name", "slug", "exercise_type",
        "is_default_for_type", "aggregation", "is_active",
    )
    list_filter = ("exercise_type", "is_default_for_type", "is_active", "aggregation")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    raw_id_fields = ("created_by",)
    inlines = [RubricCriterionInline]


@admin.register(RubricCriterion)
class RubricCriterionAdmin(admin.ModelAdmin):
    list_display = ("id", "template", "name", "code", "weight", "order")
    list_filter = ("template",)
    search_fields = ("name", "code")
    raw_id_fields = ("template",)
    inlines = [RubricBandDescriptorInline]


@admin.register(CriterionScore)
class CriterionScoreAdmin(admin.ModelAdmin):
    list_display = ("id", "feedback", "criterion", "score")
    raw_id_fields = ("feedback", "criterion")


# ---------- Inline writing annotations (docs/research/03-writing-annotations.md) ----------
@admin.register(WritingAnnotation)
class WritingAnnotationAdmin(admin.ModelAdmin):
    list_display = (
        "id", "submission", "author", "category",
        "start_offset", "end_offset", "is_acknowledged", "created_at",
    )
    list_filter = ("category", "is_acknowledged")
    search_fields = ("comment", "quoted_text")
    raw_id_fields = ("submission", "author")


# ---------- Pronunciation practice (docs/research/04-pronunciation-practice.md) ----------
@admin.register(PronunciationDrill)
class PronunciationDrillAdmin(admin.ModelAdmin):
    list_display = (
        "id", "target_text", "drill_type", "difficulty_level",
        "module", "created_by", "created_at",
    )
    list_filter = ("drill_type", "difficulty_level")
    search_fields = ("target_text", "contrast_text", "phoneme_hint")
    raw_id_fields = ("module", "created_by")


@admin.register(PronunciationAttempt)
class PronunciationAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "id", "drill", "student", "overall_score",
        "accuracy_score", "fluency_score", "engine", "created_at",
    )
    list_filter = ("engine",)
    raw_id_fields = ("drill", "student")
    readonly_fields = (
        "overall_score", "accuracy_score", "fluency_score",
        "completeness_score", "prosody_score", "word_results",
        "engine", "engine_metadata", "created_at",
    )


# ---------- Meetings ----------
@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = (
        "id", "title", "klass", "status", "scheduled_at",
        "started_at", "ended_at", "created_by", "created_at",
    )
    list_filter = ("status",)
    search_fields = ("title",)
    raw_id_fields = ("klass", "created_by")


@admin.register(AiInsight)
class AiInsightAdmin(admin.ModelAdmin):
    list_display = ("id", "submission", "kind", "engine", "requested_by", "created_at")
    list_filter = ("kind", "engine")
    search_fields = ("submission__id", "submission__student__email")
    readonly_fields = ("payload", "created_at")
