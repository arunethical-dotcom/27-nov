from urllib.parse import parse_qs
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel import Session, select
from sqlalchemy import text
from starlette.exceptions import HTTPException

from app.db import Product, SDSVersion, Session as WorkSession
from app.schemas import DISCLAIMER
from app.sessions import add_product, read_session
from app.revisions import pending_versions, version_labels

LABELS = {"CONFLICT":"CONFLICT", "UNRESOLVED":"UNRESOLVED",
          "NO_CONFLICT_FOUND":"NO CONFLICT FOUND in these SDS sections"}


async def fields(request):
    if request.headers.get("content-type","").split(";")[0] == "application/json":
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(422, "Invalid JSON")
        if not isinstance(body,dict):
            raise HTTPException(422, "Expected a JSON object")
        return body, True
    return {k:v[0] for k,v in parse_qs((await request.body()).decode()).items()}, False


def routes(engine, templates):
    router = APIRouter()
    def render(request, name, **context):
        return templates.TemplateResponse(request=request, name=name,
            context=dict(disclaimer=DISCLAIMER, labels=LABELS, **context))

    @router.get("/sessions")
    def list_sessions(request: Request):
        with Session(engine) as db:
            return render(request,"sessions.html", sessions=db.exec(
                select(WorkSession).order_by(WorkSession.id.desc())).all(), pending=pending_versions(db))

    @router.post("/sessions")
    async def create(request: Request):
        data, json_mode = await fields(request)
        name = data.get("name","Untitled session")
        if not isinstance(name,str) or not 1 <= len(name.strip()) <= 120:
            raise HTTPException(422, "Enter a session name of 1–120 characters")
        with Session(engine) as db, db.begin():
            db.execute(text("BEGIN IMMEDIATE"))
            work = WorkSession(name=name.strip())
            db.add(work)
            db.flush()
            sid = work.id
            if data.get("demo") == "fixtures":
                for code in ("ASTER-100","BOREAL-200","CIRRUS-300","DAHLIA-400","EMBER-500","FERN-600"):
                    add_product(db,sid,code,locked=True)
                    db.flush()
        if json_mode:
            return JSONResponse({"id":sid,"name":name.strip(),"disclaimer":DISCLAIMER},status_code=201,
                                headers={"Location":f"/sessions/{sid}"})
        return RedirectResponse(f"/sessions/{sid}",303)

    def matrix(request,db,sid,partial=False):
        work,report,links = read_session(db,sid)
        codes = {c.product.code for c in report.chemicals}
        choices = [p for p in db.exec(select(Product).join(SDSVersion).where(
            SDSVersion.status=="active").order_by(Product.name)) if p.code not in codes]
        return render(request,"matrix_panel.html" if partial else "matrix.html",
                      work=work,report=report,links=links,choices=choices,pending=pending_versions(db))

    @router.post("/sessions/{sid}/products")
    async def add(request: Request,sid:int):
        data,json_mode = await fields(request)
        code = data.get("code")
        if not isinstance(code,str) or not code.strip():
            raise HTTPException(422, "Enter a product code")
        with Session(engine) as db, db.begin():
            add_product(db,sid,code.strip())
            db.flush()
            if json_mode:
                return read_session(db,sid)[1].model_dump()
            if request.headers.get("HX-Request") == "true":
                return matrix(request,db,sid,True)
        return RedirectResponse(f"/sessions/{sid}",303)

    @router.get("/sessions/{sid}")
    def show(request: Request,sid:int):
        with Session(engine) as db:
            db.execute(text("BEGIN"))
            return matrix(request,db,sid)

    @router.get("/sessions/{sid}/pairs/{rid}")
    def detail(request:Request,sid:int,rid:int):
        with Session(engine) as db:
            db.execute(text("BEGIN"))
            work,_,links = read_session(db,sid)
            for result_id,pair in links.values():
                if result_id == rid:
                    return render(request,"pair.html",work=work,pair=pair,vlabels=version_labels(db))
            raise HTTPException(404,"Pair not found in session")

    @router.get("/sessions/{sid}/chemicals/{code}")
    def chemical(request:Request,sid:int,code:str):
        with Session(engine) as db:
            db.execute(text("BEGIN"))
            work,report,_ = read_session(db,sid)
            for card in report.chemicals:
                if card.product.code == code:
                    return render(request,"chemical.html",work=work,card=card,vlabels=version_labels(db))
            raise HTTPException(404,"Product not found in session")

    @router.get("/sessions/{sid}/download")
    def download(sid:int):
        with Session(engine) as db:
            db.execute(text("BEGIN"))
            return JSONResponse(read_session(db,sid)[1].model_dump(), headers={
                "Content-Disposition":f'attachment; filename="session-{sid}.json"'})
    return router
