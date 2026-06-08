# FastAPI → Django + DRF migration

The FastAPI app (`legacy_fastapi/`, formerly `app/`) is replaced by a Django +
Django REST Framework project. Same Postgres DB (`english_app`), same `.env`.

## Layout

```
config/            Django project (settings, urls, wsgi/asgi)
core/              single app: models, serializers, permissions, views, admin
  management/commands/seed_demo.py   demo seeder (port of old app/seed.py)
manage.py
legacy_fastapi/    OLD FastAPI code, retired — kept for reference, safe to delete
```

## Run

```bash
pip install -r requirements.txt          # into the venv
python manage.py migrate                  # build schema
python manage.py seed_demo                # demo data (password: password123)
python manage.py createsuperuser          # optional extra admin
python manage.py runserver                # http://127.0.0.1:8000
```

- API root + browsable API: `/api/`
- Admin (the goal): `/admin/`  — log in as `admin@english.app` / `password123`

## Auth

JWT via `djangorestframework-simplejwt`. Login takes JSON, not form-encoded:

```
POST /api/auth/login        {"email","password"} -> {access_token, refresh_token, token_type}
POST /api/auth/token        simplejwt pair (email+password) -> {access, refresh}
POST /api/auth/token/refresh {"refresh"} -> {access}
POST /api/auth/register     {email,password,full_name,role,avatar_url?}
```

Send `Authorization: Bearer <access_token>`.

## Endpoint map (old FastAPI → new DRF, all under /api/)

| FastAPI | Django + DRF |
|---|---|
| `POST /auth/register` | `POST /api/auth/register` |
| `POST /auth/login` (form) | `POST /api/auth/login` (json) |
| `GET/PUT /users/me` | `GET/PUT/PATCH /api/users/me/` |
| `GET /users/` (admin) | `GET /api/users/` |
| `GET/PUT/DELETE /users/{id}` | `…/api/users/{id}/` |
| `… /classes` CRUD | `/api/classes/` |
| `POST/GET /classes/{id}/students` | `/api/classes/{id}/students/` |
| `DELETE /classes/{id}/students/{sid}` | `DELETE /api/classes/{id}/students/{sid}/` |
| `…/classes/{id}/lesson-plans` | `/api/classes/{id}/lesson-plans/` |
| `…/classes/{id}/assignments` | `/api/classes/{id}/assignments/` |
| `… /modules` CRUD | `/api/modules/` |
| `…/modules/{id}/exercises` | `/api/modules/{id}/exercises/` |
| `GET/DELETE /modules/exercises/{id}` | `/api/exercises/{id}/` |
| `GET /modules/exercises/{id}/submissions` | `GET /api/exercises/{id}/submissions/` |
| `POST /modules/submissions` | `POST /api/submissions/` |
| `GET /modules/submissions/me` | `GET /api/submissions/me/` |
| `POST /modules/feedback` | `POST /api/feedback/` |
| `GET /modules/submissions/{id}/feedback` | `GET /api/submissions/{id}/feedback/` |
| `PUT /modules/progress` | `POST /api/progress/` (upsert) |
| `GET /modules/progress/me` | `GET /api/progress/me/` |

## Behavior notes / intentional changes

- **Passwords reset.** Old passlib-bcrypt hashes are not Django-compatible; the
  DB was rebuilt and reseeded. Everyone is `password123` again.
- **Admin role = Django superuser.** Seeded admin has `is_staff`/`is_superuser`,
  so it can use `/admin/`.
- **Suspended users**: can obtain a token but every protected endpoint returns
  403 (mirrors the old `get_current_user` check).
- **Progress upsert** moved from `PUT` to `POST /api/progress/` (DRF convention).
- **List responses are bare arrays** (pagination disabled) to match the old shape.
- The `klass` model field is the FK to `Class` (`class` is a keyword); it is
  exposed on the wire as `class_id`.

## Carried-over security gaps (NOT fixed — faithful port; see REFACTOR_PLAN.md)

These behave exactly as the FastAPI app did and are worth a follow-up:
- `register` accepts any `role`, including `admin` (self-escalation).
- `GET /api/submissions/{id}/feedback/` has no ownership check.
- Teachers can reassign a class's `teacher_id` via update.
