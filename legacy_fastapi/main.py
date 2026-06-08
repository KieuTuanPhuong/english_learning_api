from contextlib import asynccontextmanager
from fastapi import FastAPI

from . import models  # noqa: F401  register models with Base before create_all
from .database import Base, engine
from .routers import auth_router, users_router, classes_router, modules_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="English Learning Application API",
    version="0.1.0",
    description="Demo MVP: Auth + Basic CRUD for Student/Teacher/Admin roles.",
    lifespan=lifespan,
)


@app.get("/", tags=["root"])
def root():
    return {"status": "ok", "service": "english-learning-api"}


app.include_router(auth_router.router)
app.include_router(users_router.router)
app.include_router(classes_router.router)
app.include_router(modules_router.router)
