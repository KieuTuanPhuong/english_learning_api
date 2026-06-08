# Project: English Learning Application API Demo

## 1. Context & Objective
You are an expert Python backend engineer. Your task is to build a fast, functional REST API prototype using **FastAPI**. This backend supports an English Learning Application with three user roles: Student, Teacher, and Admin.

For this demo, we are focusing strictly on **Authentication** and **Basic CRUD operations** for the core entities. Skip all AI grading integrations, email sending, and file uploading (mock file URLs as strings).

## 2. Tech Stack
* **Framework:** FastAPI
* **ORM:** SQLAlchemy 2.0
* **Database:** PostgreSQL (using `psycopg2-binary`)
* **Environment Management:** `python-dotenv`
* **Data Validation:** Pydantic V2
* **Authentication:** JWT (JSON Web Tokens) with OAuth2 Password Bearer and Passlib (bcrypt)

## 3. Required Project Structure
Please generate the code following this directory structure:

```text
app/
├── main.py              # FastAPI application instance and router inclusion
├── database.py          # SQLAlchemy engine (Postgres), session, and Base class
├── models.py            # SQLAlchemy database models (map the provided DBML here)
├── schemas.py           # Pydantic models for request/response validation
├── auth.py              # JWT token generation, password hashing, and dependencies
└── routers/
    ├── auth_router.py   # Login and registration endpoints
    ├── users_router.py  # User profile CRUD
    ├── classes_router.py# Class management CRUD
    └── modules_router.py# Learning modules and exercises CRUD