# English Learning API: Endpoints and Implemented Functions Documentation

This document contains a comprehensive register of all API endpoints and implemented Python functions, classes, and methods within the active Django-based backend application.

---

## I. API Endpoints

These routes are configured in [config/urls.py](file:///Users/macbook/USTH/english-learning-api/config/urls.py) and map incoming HTTP requests to their respective controllers, views, or viewsets.

### 1. Base, Authentication & Documentation API
These endpoints handle base routing, authentication services, and interactive OpenAPI documentation.

| HTTP Method | Endpoint | View / Handler | Permissions Required | Description |
| :--- | :--- | :--- | :--- | :--- |
| **GET** | `/` | `root` function | Allow Any | Basic service health/sanity check |
| **GET** | `/admin/` | `admin.site.urls` | Staff / Admin | Django default administrative panel |
| **POST** | `/api/auth/register` | [RegisterView](file:///Users/macbook/USTH/english-learning-api/core/views.py#L89) | Allow Any | Register a new user |
| **POST** | `/api/auth/login` | [LoginView](file:///Users/macbook/USTH/english-learning-api/core/views.py#L118) | Allow Any | Log in a user and obtain token pair |
| **POST** | `/api/auth/token` | `TokenObtainPairView` | Allow Any | SimpleJWT default token generation (email + password) |
| **POST** | `/api/auth/token/refresh` | `TokenRefreshView` | Allow Any | SimpleJWT default token refresh |
| **GET** | `/api/schema/` | `SpectacularAPIView` | Allow Any | Serves the raw OpenAPI v3 schema |
| **GET** | `/api/docs/` | `SpectacularSwaggerView` | Allow Any | Renders the interactive Swagger UI documentation |
| **GET** | `/api/redoc/` | `SpectacularRedocView` | Allow Any | Renders the interactive ReDoc documentation UI |

---

### 2. ViewSet-Based RESTful APIs (Registered under `/api/`)
These endpoints are registered via `DefaultRouter` in [config/urls.py](file:///Users/macbook/USTH/english-learning-api/config/urls.py#L22-L31) and dynamically mapped to standard CRUD actions and custom routes.

#### A. Users (`/api/users/`)
*Mapped to [UserViewSet](file:///Users/macbook/USTH/english-learning-api/core/views.py#L143)*

| HTTP Method | Endpoint | ViewSet Method | Permissions Required | Description |
| :--- | :--- | :--- | :--- | :--- |
| **GET** | `/api/users/` | `list` | Active User | Lists users (filtered by role scope) |
| **POST** | `/api/users/` | `create` | Admin Only | Creates a user (Admin/Teacher/Student) |
| **GET** | `/api/users/me/` | `me` | Active User | Fetches details of the logged-in user |
| **GET** | `/api/users/<id>/` | `retrieve` | Active User | Retrieves details of a specific user |
| **PUT** | `/api/users/<id>/` | `update` | Active User | Updates details of a specific user |
| **PATCH** | `/api/users/<id>/` | `partial_update` | Active User | Partially updates details of a user |
| **DELETE** | `/api/users/<id>/` | `destroy` | Admin Only | Deletes a user profile |

#### B. Classes (`/api/classes/`)
*Mapped to [ClassViewSet](file:///Users/macbook/USTH/english-learning-api/core/views.py#L230)*

| HTTP Method | Endpoint | ViewSet Method | Permissions Required | Description |
| :--- | :--- | :--- | :--- | :--- |
| **GET** | `/api/classes/` | `list` | Active User | Lists classes (Admin sees all; Teachers see own; Students see enrolled) |
| **POST** | `/api/classes/` | `create` | Teacher / Admin | Creates a new class |
| **GET** | `/api/classes/<id>/` | `retrieve` | Active User | Retrieves a class |
| **PUT** | `/api/classes/<id>/` | `update` | Class Owner / Admin | Replaces class details |
| **PATCH** | `/api/classes/<id>/` | `partial_update` | Class Owner / Admin | Partially updates class details |
| **DELETE** | `/api/classes/<id>/` | `destroy` | Class Owner / Admin | Deletes a class |
| **GET** | `/api/classes/<pk>/students/` | `students` (GET) | Active User | Lists all students enrolled in a class |
| **POST** | `/api/classes/<pk>/students/` | `students` (POST) | Class Owner / Admin | Enrolls a student into a class |
| **DELETE** | `/api/classes/<pk>/students/<student_id>/` | `remove_student` | Class Owner / Admin | Removes a student from a class |
| **GET** | `/api/classes/<pk>/lesson-plans/` | `lesson_plans` (GET) | Active User | Lists lesson plans associated with a class |
| **POST** | `/api/classes/<pk>/lesson-plans/` | `lesson_plans` (POST) | Class Owner / Admin | Creates a lesson plan for a class |
| **GET** | `/api/classes/<pk>/assignments/` | `assignments` (GET) | Active User | Lists assignments associated with a class |
| **POST** | `/api/classes/<pk>/assignments/` | `assignments` (POST) | Class Owner / Admin | Assigns a module/exercise to a class |

#### C. Modules (`/api/modules/`)
*Mapped to [ModuleViewSet](file:///Users/macbook/USTH/english-learning-api/core/views.py#L375)*

| HTTP Method | Endpoint | ViewSet Method | Permissions Required | Description |
| :--- | :--- | :--- | :--- | :--- |
| **GET** | `/api/modules/` | `list` | Active User | Lists all learning modules |
| **POST** | `/api/modules/` | `create` | Teacher / Admin | Creates a new learning module |
| **GET** | `/api/modules/<id>/` | `retrieve` | Active User | Retrieves a single module |
| **PUT** | `/api/modules/<id>/` | `update` | Module Owner / Admin | Updates a module |
| **PATCH** | `/api/modules/<id>/` | `partial_update` | Module Owner / Admin | Partially updates a module |
| **DELETE** | `/api/modules/<id>/` | `destroy` | Admin Only | Deletes a module |
| **GET** | `/api/modules/<pk>/exercises/` | `exercises` (GET) | Active User | Lists exercises within a module |
| **POST** | `/api/modules/<pk>/exercises/` | `exercises` (POST) | Module Owner / Admin | Creates an exercise inside a module |

#### D. Exercises (`/api/exercises/`)
*Mapped to [ExerciseViewSet](file:///Users/macbook/USTH/english-learning-api/core/views.py#L431)*

| HTTP Method | Endpoint | ViewSet Method | Permissions Required | Description |
| :--- | :--- | :--- | :--- | :--- |
| **GET** | `/api/exercises/<id>/` | `retrieve` | Active User | Retrieves exercise data |
| **DELETE** | `/api/exercises/<id>/` | `destroy` | Teacher / Admin | Deletes an exercise |
| **GET** | `/api/exercises/<pk>/submissions/` | `submissions` | Teacher / Admin | Lists student submissions for an exercise |

#### E. Submissions (`/api/submissions/`)
*Mapped to [SubmissionViewSet](file:///Users/macbook/USTH/english-learning-api/core/views.py#L457)*

| HTTP Method | Endpoint | ViewSet Method | Permissions Required | Description |
| :--- | :--- | :--- | :--- | :--- |
| **POST** | `/api/submissions/` | `create` | Student Only | Submits answers (auto-grades if receptive type) |
| **GET** | `/api/submissions/me/` | `me` | Active User | Lists current student's own submissions |
| **GET** | `/api/submissions/inbox/` | `inbox` | Teacher / Admin | Teacher's inbox of submissions requiring grading |
| **GET** | `/api/submissions/<pk>/feedback/` | `feedback` | Evaluator / Owner | Lists feedback for a submission |
| **POST** | `/api/submissions/<pk>/ai-evaluate/` | `ai_evaluate` | Teacher / Admin | Synchronously trigger AI scoring/feedback |
| **POST** | `/api/submissions/ai-practice/` | `ai_practice` | Student Only | Submits off-assignment practice for immediate AI feedback |

#### F. Feedback (`/api/feedback/`)
*Mapped to [FeedbackViewSet](file:///Users/macbook/USTH/english-learning-api/core/views.py#L636)*

| HTTP Method | Endpoint | ViewSet Method | Permissions Required | Description |
| :--- | :--- | :--- | :--- | :--- |
| **POST** | `/api/feedback/` | `create` | Teacher / Admin | Submit manual or scripted evaluation for a submission |

#### G. Progress (`/api/progress/`)
*Mapped to [ProgressViewSet](file:///Users/macbook/USTH/english-learning-api/core/views.py#L672)*

| HTTP Method | Endpoint | ViewSet Method | Permissions Required | Description |
| :--- | :--- | :--- | :--- | :--- |
| **POST** | `/api/progress/` | `create` | Student Only | Upserts completion progress for a module |
| **GET** | `/api/progress/me/` | `me` | Active User | Lists current student's module progress percentages |

#### H. Study Materials (`/api/study-materials/`)
*Mapped to [StudyMaterialViewSet](file:///Users/macbook/USTH/english-learning-api/core/views.py#L716)*

| HTTP Method | Endpoint | ViewSet Method | Permissions Required | Description |
| :--- | :--- | :--- | :--- | :--- |
| **GET** | `/api/study-materials/` | `list` | Active User | Lists study documents (official and class-scoped) |
| **GET** | `/api/study-materials/<id>/` | `retrieve` | Active User | Retrieves a study document |
| **POST** | `/api/study-materials/` | `create` | Admin Only | Uploads/registers a study document |
| **PUT/PATCH/DELETE** | `/api/study-materials/<id>/` | standard actions | Admin Only | Update/Delete a study document |

#### I. AI Model Registry (`/api/ai-models/`)
*Mapped to [AiModelViewSet](file:///Users/macbook/USTH/english-learning-api/core/views.py#L737)*

| HTTP Method | Endpoint | ViewSet Method | Permissions Required | Description |
| :--- | :--- | :--- | :--- | :--- |
| **GET/POST/PUT/PATCH/DELETE** | `/api/ai-models/` | standard actions | Admin Only | CRUD operations for registering or configuring AI models |

---

### 3. Aggregate Analytics, Reports & Audit
These are custom `APIView` endpoints handling platform-wide monitoring, reporting, and dashboard actions.

| HTTP Method | Endpoint | Handler Class | Permissions Required | Description |
| :--- | :--- | :--- | :--- | :--- |
| **GET** | `/api/dashboard/` | [DashboardView](file:///Users/macbook/USTH/english-learning-api/core/views.py#L792) | Active User | Aggregate metrics dashboard (Student/Teacher/Admin modes) |
| **GET** | `/api/reports/grades` | [GradeReportView](file:///Users/macbook/USTH/english-learning-api/core/views.py#L900) | Teacher / Admin | Exports grade CSV (or PDF if enabled) for class |
| **GET** | `/api/admin/activity/` | [AdminActivityView](file:///Users/macbook/USTH/english-learning-api/core/views.py#L976) | Admin Only | Consolidated audit log of admin activity and user registrations |
| **GET** | `/api/admin/health/` | [AdminHealthView](file:///Users/macbook/USTH/english-learning-api/core/views.py#L1033) | Admin Only | Probe tracking database latency and model entity counts |

---

## II. Implemented Python Functions & Methods

Detailed lookup of internal logic, models, services, and class methods.

### 1. View Controllers & Route Logic ([core/views.py](file:///Users/macbook/USTH/english-learning-api/core/views.py))

*   **Global Views Helpers**:
    *   `_perms(*extra)`: Helper to build the REST framework permission list. Appends custom permissions to the base `IsActiveUser` validator.
    *   `_log_status_change(admin, target, new_status)`: System log writer recording changes to a user's account state (e.g. active to suspended).
    *   `_ungraded_for_teacher(teacher)`: Returns a queryset of submissions in the teacher's class scope that do not have associated feedback.
*   **ClassViewSet**:
    *   `_require_class_owner(self, request, cls)`: Internal validation method enforcing that the request sender is either the teacher who owns the class or an Admin.
*   **DashboardView**:
    *   `_student(self, user)`: Gathers student-specific aggregate metrics (due assignments, in-progress modules, recent feedback).
    *   `_teacher(self, user)`: Gathers teacher-specific metrics (enrolled counts, ungraded counts, owned class details).
    *   `_admin(self)`: Gathers admin metrics (counts by user role, database totals, list of recent registrations, submissions, and feedback).
*   **GradeReportView**:
    *   `_rows(self, cls)`: Prepares dictionary row matrices for class submissions, including scoring details.
    *   `_csv(self, cls, rows)`: Serializes row dictionaries and constructs a CSV download response.
    *   `_pdf(self, cls, rows)`: Hook for PDF generation (returns a `501 NotImplementedError` unless the `REPORTS_PDF_ENABLED` flag is toggled on in settings).
*   **AdminActivityView**:
    *   `get(self, request)`: Collates logs, new users, recent submissions, and feedback entries sorted by timestamp into a single audit feed.
*   **AdminHealthView**:
    *   `get(self, request)`: Runs a latency test (`SELECT 1`) on the default DB connection and aggregates model-level counts.

---

### 2. Database Models & Managers ([core/models.py](file:///Users/macbook/USTH/english-learning-api/core/models.py))

*   **UserManager**:
    *   `create_user(self, email, password=None, **extra)`: Validates role scopes, normalizes emails, creates the user record, hashes passwords via `set_password()`, and persists the user.
    *   `create_superuser(self, email, password=None, **extra)`: Standard superuser generator setting staff/superuser flags and invoking `create_user`.
*   **Submission**:
    *   `grade(self)`: Computes automated scores (0–100) for receptive exercise types by comparing client options against the correct answer keys in the database.
*   **AiModel**:
    *   `active(cls)`: Classmethod returning the current configured `AiModel` where `is_active=True`.

---

### 3. AI Service & Integration Layer ([core/ai/](file:///Users/macbook/USTH/english-learning-api/core/ai/))

*   **Service Orchestrator** ([core/ai/service.py](file:///Users/macbook/USTH/english-learning-api/core/ai/service.py)):
    *   `get_backend()`: Factory resolving whether to run evaluation using the `MockBackend` or `RealBackend` based on configuration settings.
    *   `grade_submission(submission) -> dict`: Dispatches the grading evaluation task to either writing/speaking routines based on the submission's exercise type.
    *   `evaluate_submission(submission) -> Feedback`: Synchronously evaluates a submission, writes a new `Feedback` model entry containing the score and notes, and flags the submission as `AI_GRADED`.
*   **AI Backends** ([core/ai/backends.py](file:///Users/macbook/USTH/english-learning-api/core/ai/backends.py)):
    *   `_clamp(score: Decimal) -> Decimal`: Restricts scores between 0 and 100 and rounds to two decimal places.
    *   **BaseAIBackend**:
        *   `__init__(self, ai_model=None)`: Binds the active AI configuration schema.
        *   `strictness` property: Reads active severity values (Lenient, Standard, Strict) used to scale calculated scores.
        *   `grade_writing(self, submission)`: Virtual grading template method.
        *   `transcribe_and_score_speaking(self, submission)`: Virtual speaking/audio template method.
    *   **MockBackend**:
        *   `grade_writing(self, submission)`: Calculates deterministic scores based on keyword presence and word length.
        *   `transcribe_and_score_speaking(self, submission)`: Simulates transcription and calculates deterministic speaking scores derived from audio URL properties.
    *   **RealBackend**:
        *   `grade_writing(self, submission)`: Stub for production API grading calls (currently raises `NotImplementedError`).
        *   `transcribe_and_score_speaking(self, submission)`: Stub for Whisper STT and LLM speech analysis integration (currently raises `NotImplementedError`).

---

### 4. Custom Permissions ([core/permissions.py](file:///Users/macbook/USTH/english-learning-api/core/permissions.py))

*   **IsActiveUser**: Restricts API calls if an authenticated user's account status is set to `SUSPENDED`.
*   **_RolePermission**: Evaluates role membership matching against allowed configurations.
*   **IsAdmin / IsTeacherOrAdmin / IsStudent**: Subclasses executing role check assertions.
*   **can_view_submission_feedback(user, submission) -> bool**: Standard helper evaluating permission to view feedback. Grants permissions to:
    1. Administrators
    2. The student who owns the submission
    3. The teacher who owns the class associated with the submission's assignment
    4. The teacher who reviewed and left the feedback

---

### 5. Custom Django Commands ([core/management/](file:///Users/macbook/USTH/english-learning-api/core/management/))

*   **seed_demo.py** ([seed_demo.py](file:///Users/macbook/USTH/english-learning-api/core/management/commands/seed_demo.py)):
    *   `Command.handle(self, *args, **options)`: Command handler executing database cleanup and seeding sample users, classes, materials, exercises, and mock submissions.
