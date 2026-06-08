# UI Flow Design Brief — English Learning App (MVP)

**Audience:** UI/UX design agents
**Backend status:** REST API complete
**Your task:** Design the end-to-end UI flows for **Student**, **Teacher**, and **Admin** roles. Deliver screen inventory, user journeys, wireframes, and a clickable prototype.

---

## 1. Product Context

An English learning web application with three roles. Students complete writing/speaking exercises; teachers create content, assign work, and grade; admins govern users and the platform.

**MVP constraints (do not design for these — explicitly out of scope):**
- No AI-graded feedback (only manual teacher feedback)
- No real file uploads (audio/avatar URLs are plain strings)
- No email/notification system
- No payments, no marketplace

---

## 2. Roles & Capabilities

### Student
- Register/login, manage own profile
- Browse learning modules and exercises (read-only catalog)
- Submit writing (text) or speaking (audio URL) responses to exercises
- View own submissions and the feedback teachers give
- Track progress per module (completion %)
- View classes they are enrolled in, see assignments, due dates

### Teacher
- Register/login, manage own profile
- Create + manage their own classes (rename, archive)
- Enroll/unenroll students
- Create lesson plans tied to a class
- Create learning modules they own (title, description, difficulty)
- Create exercises within modules (writing, speaking, or quiz type)
- Assign exercises to a class with a due date
- View submissions for their exercises
- Grade submissions: enter score + comments (feedback)

### Admin
- All teacher capabilities
- Full user management: list, view, edit (incl. suspend/activate), delete any user
- Delete any class, module, or exercise
- (Future) View system stats and logs — leave room in dashboard layout, but no live endpoints yet

---

## 3. Domain Entities (Data Model Summary)

These are the nouns the UI manipulates. Each maps to a backend table; field names below match the API contract exactly.

| Entity | Key fields |
|---|---|
| **User** | id, email, full_name, avatar_url, role (`student`/`teacher`/`admin`), status (`active`/`suspended`/`inactive`) |
| **Class** | id, class_name, teacher_id, academic_year |
| **ClassStudent** | class_id, student_id, joined_at (enrollment link) |
| **LessonPlan** | id, class_id, title, objectives, start_date, end_date |
| **LearningModule** | id, title, description, difficulty_level (`beginner`/`intermediate`/`advanced`), created_by |
| **Exercise** | id, module_id, title, exercise_type (`writing`/`speaking`/`quiz`), prompt_text, audio_prompt_url |
| **Assignment** | id, class_id, exercise_id, assigned_by, due_date |
| **Submission** | id, exercise_id, assignment_id (nullable = self-practice), student_id, submission_type, writing_text, audio_recording_url, submitted_at |
| **Feedback** | id, submission_id, reviewer_id, score (0–100), comments |
| **StudentModuleProgress** | id, student_id, module_id, completion_percentage (0.00–100.00), last_accessed_at |

---

## 4. Required Screens (per role)

Design each screen with empty, loading, error, and populated states.

### Shared (all roles)
1. **Landing / login** — email + password
2. **Register** — email, password, full_name, role selector (student/teacher; admin is provisioned, not self-signup)
3. **Profile** — view + edit full_name, avatar_url; show role badge and status
4. **Logout** confirmation

### Student
5. **Student home / dashboard** — assigned exercises due soon, in-progress modules, recent feedback
6. **My classes** — list of enrolled classes, each linking to class detail
7. **Class detail (student view)** — class name, teacher, lesson plans (read-only), active assignments with due dates
8. **Module catalog** — grid/list of all learning modules with difficulty filter and search
9. **Module detail** — description, list of exercises, progress bar (the student's own completion %)
10. **Exercise viewer** — prompt, optional audio prompt URL, response area:
    - Writing → multiline text input
    - Speaking → URL input (mock upload)
    - Quiz → text input (free-form for MVP)
    - Submit button
11. **My submissions** — list of submissions, status badge (graded / awaiting feedback), score if graded
12. **Submission detail** — original prompt, what student submitted, all feedback entries (reviewer, score, comments, date)

### Teacher
13. **Teacher dashboard** — own classes count, pending submissions to grade, recent activity
14. **My classes** — list + "Create class" CTA
15. **Class detail (teacher view)** — roster (enrolled students with add/remove), lesson plans CRUD, assignments CRUD
16. **Create/edit class** — class_name, academic_year
17. **Enroll students** — search users by email/name, filter to students, add to class
18. **Lesson plan editor** — title, objectives (rich text or plaintext), start/end date
19. **My learning modules** — list of modules created by this teacher + "Create module" CTA
20. **Module editor** — title, description, difficulty selector
21. **Exercises tab inside module editor** — list of exercises, "Add exercise" form (type selector, prompt, optional audio URL)
22. **Assignment creator** — pick exercise from own modules, pick class, set due_date
23. **Submissions inbox** — submissions across teacher's exercises, filterable by exercise/class/status (graded vs ungraded)
24. **Grading screen** — show student response side-by-side with prompt, score input (0–100), comments textarea, submit feedback

### Admin
25. **Admin dashboard** — user counts by role, total classes, total modules, total submissions (placeholder cards if no aggregate endpoint yet — note for backend follow-up)
26. **User management** — table of all users with filters (role, status), inline status toggle, edit/delete actions
27. **User detail / edit** — full profile, status (active/suspended/inactive), audit fields (created_at, updated_at)
28. **All classes** — read across all teachers, delete capability
29. **All modules** — read across all creators, delete capability

---

## 5. Key User Journeys

Design at least these flows end-to-end. Show every screen transition.

### J1 — Student completes a writing assignment
Login → student dashboard → click assignment → exercise viewer → type response → submit → confirmation toast → returns to dashboard with assignment marked "submitted"

### J2 — Student practices independently (no assignment)
Login → module catalog → pick module → see exercises → pick exercise → submit → view in "My submissions"

### J3 — Teacher creates and assigns an exercise
Login → my modules → create module → add exercise → my classes → class detail → "New assignment" → pick exercise + due date → confirm → class roster sees it next login

### J4 — Teacher grades submissions
Login → submissions inbox → filter "ungraded" → open submission → enter score + comments → save → next ungraded auto-loads (or returns to inbox)

### J5 — Student receives feedback
Login → notification badge on "My submissions" → submission detail → read feedback

### J6 — Admin suspends a misbehaving user
Login → user management → search user → edit → status = "suspended" → save → confirmation. (Suspended user gets 403 from API on next request.)

### J7 — Teacher enrolls a student in a class
Login → class detail → roster tab → "Add student" → search by email → select → confirm → roster updates

---

## 6. Endpoint → Screen Map (cheat sheet)

Designers don't have to memorize endpoints, but knowing what data each screen needs helps with state design. Format: **Screen** ← `METHOD /path`.

- Login ← `POST /auth/login` (form: username=email, password)
- Register ← `POST /auth/register`
- Profile (read) ← `GET /users/me`
- Profile (edit) ← `PUT /users/me`
- Student "My classes" ← `GET /classes/` (server filters by enrollment)
- Class detail (roster) ← `GET /classes/{id}/students`
- Class lesson plans ← `GET /classes/{id}/lesson-plans`
- Class assignments ← `GET /classes/{id}/assignments`
- Module catalog ← `GET /modules/`
- Module detail ← `GET /modules/{id}` + `GET /modules/{id}/exercises`
- Exercise viewer ← `GET /modules/exercises/{id}`
- Submit exercise ← `POST /modules/submissions`
- My submissions ← `GET /modules/submissions/me`
- Feedback on a submission ← `GET /modules/submissions/{id}/feedback`
- My progress ← `GET /modules/progress/me`; update ← `PUT /modules/progress`
- Teacher creates class ← `POST /classes/`
- Teacher enrolls student ← `POST /classes/{id}/students`
- Teacher creates module ← `POST /modules/`
- Teacher creates exercise ← `POST /modules/{module_id}/exercises`
- Teacher creates assignment ← `POST /classes/{id}/assignments`
- Teacher views all submissions for an exercise ← `GET /modules/exercises/{id}/submissions`
- Teacher grades ← `POST /modules/feedback`
- Admin lists users ← `GET /users/`
- Admin edits user ← `PUT /users/{id}`
- Admin deletes user ← `DELETE /users/{id}`

Full OpenAPI spec lives at `http://127.0.0.1:8000/docs` once the server runs.

---

## 7. Design System Direction

Pick consistent choices and document them. Recommendations, not mandates:

- **Look & feel:** clean, education-focused, not gamified-childish. Audience is teens to adults.
- **Primary actions:** prominent CTAs for "Submit" (student) and "Grade" (teacher) — these are the most frequent.
- **Role differentiation:** subtle color accent per role in the topbar (e.g., student=blue, teacher=green, admin=red) so users always know which lens they're in.
- **Status badges:** consistent visual language for `active`/`suspended`/`inactive`, `graded`/`ungraded`, `beginner`/`intermediate`/`advanced`, `writing`/`speaking`/`quiz`.
- **Empty states:** every list view needs a useful empty state with a CTA (e.g., teacher's "No modules yet — create your first").
- **Audio mock:** since real upload is out of scope, the speaking submission UI should accept a URL with a clear note ("paste audio URL — file upload coming soon"). Same for avatar.
- **Accessibility:** WCAG AA color contrast, keyboard navigation, focus rings, alt text on icons. Audio prompts must have text alternatives.

---

## 8. Deliverables

1. **Screen inventory** — confirm coverage of the 29 screens above; flag any missing
2. **User flow diagrams** — at minimum J1, J3, J4, J6 (one diagram each)
3. **Wireframes** — low-fi for all screens
4. **Visual design** — high-fi for the 8 most-used screens: login, student dashboard, exercise viewer, my submissions, teacher dashboard, grading screen, submissions inbox, admin user management
5. **Clickable prototype** — covers J1, J3, J4 end-to-end
6. **Design tokens** — colors, typography, spacing, radii, shadows
7. **Component library** — buttons, inputs, badges, tables, modals, navigation, file/URL pickers

---

## 9. Open Questions to Resolve With Stakeholder

Mark these unresolved; do not assume:

- Mobile-first, desktop-first, or responsive parity?
- Does student need a "self-practice" path independent of any class, or must they always be enrolled?
- Should teachers be able to share modules with each other, or are modules strictly siloed per creator?
- For quiz-type exercises: free-text answer (current backend) or multiple choice (would need backend change)?
- Notification surface: in-app only, or do we plan for email later (affects design of unread states)?
- Admin: is there a future "system stats" panel? If yes, what metrics?

---

## 10. Out of Scope (do not design)

- AI grading flows or AI model management screens
- File upload widgets with real upload behavior (URL fields only)
- Email verification, password reset emails
- Payment, billing, subscription
- Public marketing site, blog, marketing pages
- Real-time chat or messaging between users
- Mobile native apps
