from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import event
from sqlmodel import create_engine
from starlette.exceptions import HTTPException

from app.db import SQLModel

DISCLAIMER = "Decision support only. Confirm with a qualified person."
templates = Jinja2Templates(directory=Path(__file__).parent / "ui")


def create_app(database_url="sqlite:///advisor.db"):
    engine = create_engine(database_url)

    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    @asynccontextmanager
    async def lifespan(app):
        SQLModel.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(lifespan=lifespan, openapi_url=None)

    @app.exception_handler(HTTPException)
    async def error(request, exc):
        return JSONResponse({"detail": exc.detail, "disclaimer": DISCLAIMER}, status_code=exc.status_code)

    @app.get("/")
    def home(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={"disclaimer": DISCLAIMER})

    return app


app = create_app()
