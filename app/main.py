from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from app.session_ui import routes
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import event, text
from sqlmodel import Session, create_engine, select
from starlette.exceptions import HTTPException

from app.db import Chunk, Product, SDSVersion, initialize_database
from app.facts import FORBIDDEN, confirm, extract, facts, passages, valid

DISCLAIMER = "Decision support only. Confirm with a qualified person."
templates = Jinja2Templates(directory=Path(__file__).parent / "ui")


def create_app(database_url="sqlite:///advisor.db"):
    engine = create_engine(database_url)

    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    @asynccontextmanager
    async def lifespan(app):
        initialize_database(engine)
        yield
        engine.dispose()

    app = FastAPI(lifespan=lifespan, openapi_url=None)

    @app.exception_handler(HTTPException)
    async def error(request, exc):
        return JSONResponse({"detail": exc.detail, "disclaimer": DISCLAIMER}, status_code=exc.status_code)

    @app.get("/")
    def home(request: Request):
        with Session(engine) as db:
            versions = db.exec(select(Product.name, SDSVersion.id, SDSVersion.version_no)
                               .join(SDSVersion).where(SDSVersion.status == "active")
                               .order_by(Product.name)).all()
        return templates.TemplateResponse(request=request, name="index.html",
                                         context={"disclaimer": DISCLAIMER, "versions": versions})

    @app.get("/versions/{version_id}/facts")
    def review(request: Request, version_id: int):
        with Session(engine) as db, db.begin():
            db.execute(text("BEGIN IMMEDIATE"))
            version = db.get(SDSVersion, version_id)
            if not version or version.status != "active":
                raise HTTPException(404, "Active version required")
            extract(db, version)
            rows = [(f, db.get(Chunk, f.chunk_id)) for f in facts(db, version_id)]
            notes = [quote for c in db.exec(select(Chunk).where(Chunk.version_id == version_id,
                     Chunk.method == "section")) if c.matches_source(version.document_text)
                     and (c.section.split(".")[0] == "7" or c.section == "10.5")
                     for _,_,quote in passages(c) if not FORBIDDEN.search(quote)]
            return templates.TemplateResponse(request=request, name="facts.html", context={
                "version": version, "product": db.get(Product, version.product_id),
                "rows": [(f,c) for f,c in rows if valid(f,c,version) and not FORBIDDEN.search(f.quote)], "notes": notes, "disclaimer": DISCLAIMER})

    @app.post("/versions/{version_id}/facts/confirm")
    async def confirm_all(request: Request, version_id: int):
        if request.headers.get("content-type","").split(";")[0] == "application/json":
            try:
                payload = await request.json()
            except ValueError:
                raise HTTPException(422, "Invalid JSON")
            user = payload.get("user") if isinstance(payload, dict) else None
        else:
            user = parse_qs((await request.body()).decode()).get("user", [""])[0]
        if not isinstance(user,str) or not user.strip() or len(user) > 120:
            raise HTTPException(422, "Enter a reviewer name")
        with Session(engine) as db, db.begin():
            db.execute(text("BEGIN IMMEDIATE"))
            version = db.get(SDSVersion, version_id)
            if not version or version.status != "active":
                raise HTTPException(404, "Active version required")
            try:
                confirm(db, version, user)
            except ValueError as exc:
                raise HTTPException(409, str(exc))
        return RedirectResponse(f"/versions/{version_id}/facts", status_code=303)

    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "ui/static"), name="static")
    app.include_router(routes(engine,templates))
    return app


app = create_app()
