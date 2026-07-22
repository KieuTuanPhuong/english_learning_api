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
    Exercise,
    Feedback,
    LearningModule,
    LessonPlan,
    Question,
    QuestionOption,
    StudentModuleProgress,
    Submission,
    User,
    StudyMaterial,
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
    list_display = ("id", "title", "difficulty_level", "created_by", "created_at")
    list_filter = ("difficulty_level",)
    search_fields = ("title",)
    raw_id_fields = ("created_by",)
    inlines = [ExerciseInline]


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0


@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "exercise_type", "module", "created_by", "created_at")
    list_filter = ("exercise_type",)
    search_fields = ("title", "prompt_text", "content_text")
    raw_id_fields = ("module", "created_by")
    inlines = [QuestionInline]


class QuestionOptionInline(admin.TabularInline):
    model = QuestionOption
    extra = 0


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("id", "exercise", "order", "text")
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
