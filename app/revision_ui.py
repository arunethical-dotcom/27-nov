"""Upload a new SDS, preview a replacement, confirm it, and read change reports."""
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlmodel import Session, select
from starlette.exceptions import HTTPException

from app.db import ChangeReport, Product, SDSVersion
from app.revisions import (RevisionError, discard, label, pending_versions, preview, replace,
                           stage_upload)
from app.schemas import DISCLAIMER

FLAG_TEXT = {"STATUS_CHANGED": "Status changed",
             "EVIDENCE_CHANGED": "Same verdict, evidence changed — review required",
             "NONE": "No change"}


def routes(engine, templates, labels):
    router = APIRouter()

    def render(request, name, status_code=200, **context):
        return templates.TemplateResponse(request=request, name=name, status_code=status_code,
                                          context=dict(disclaimer=DISCLAIMER, labels=labels,
                                                       flags=FLAG_TEXT, **context))

    @router.get("/upload")
    def upload_form(request: Request):
        with Session(engine) as db:
            return render(request, "upload.html", pending=pending_versions(db), message=None)

    @router.post("/upload")
    async def upload(request: Request, file: UploadFile = File(...), user: str = Form("")):
        data = await file.read()
        name = (user or "").strip()[:120] or "unknown"
        try:
            with Session(engine) as db, db.begin():
                db.execute(text("BEGIN IMMEDIATE"))
                outcome, version_id, message = stage_upload(db, data, file.filename or "upload.pdf", name)
        except RevisionError as exc:
            with Session(engine) as db:
                return render(request, "upload.html", 422, pending=pending_versions(db), message=str(exc))
        if outcome == "pending":
            return RedirectResponse(f"/versions/{version_id}/preview", 303)
        if outcome == "new":
            return RedirectResponse(f"/versions/{version_id}/facts", 303)
        with Session(engine) as db:
            code = 409 if outcome == "duplicate" else 422
            return render(request, "upload.html", code, pending=pending_versions(db), message=message)

    @router.get("/versions/{version_id}/preview")
    def show_preview(request: Request, version_id: int):
        with Session(engine) as db:
            try:
                summary = preview(db, version_id)
            except RevisionError as exc:
                raise HTTPException(404, str(exc))
        return render(request, "preview.html", s=summary)

    @router.post("/versions/{version_id}/replace")
    def confirm_replace(version_id: int, user: str = Form("")):
        try:
            with Session(engine) as db, db.begin():
                db.execute(text("BEGIN IMMEDIATE"))
                report_id = replace(db, version_id, user)
        except RevisionError as exc:
            raise HTTPException(422, str(exc))
        return RedirectResponse(f"/change-reports/{report_id}", 303)

    @router.post("/versions/{version_id}/discard")
    def keep_current(version_id: int):
        try:
            with Session(engine) as db, db.begin():
                db.execute(text("BEGIN IMMEDIATE"))
                discard(db, version_id)
        except RevisionError as exc:
            raise HTTPException(422, str(exc))
        return RedirectResponse("/upload", 303)

    @router.get("/change-reports")
    def reports(request: Request):
        with Session(engine) as db:
            rows = db.exec(select(ChangeReport).order_by(ChangeReport.id.desc())).all()
            items = [dict(id=r.id, product=db.get(Product, r.product_id).name,
                          old=label(db.get(SDSVersion, r.old_version_id)),
                          new=label(db.get(SDSVersion, r.new_version_id)),
                          by=r.confirmed_by, at=r.confirmed_at,
                          changed=sum(1 for a in r.affected_pairs_json if a["flag"] != "NONE"))
                     for r in rows]
        return render(request, "reports.html", items=items)

    @router.get("/change-reports/{report_id}")
    def report(request: Request, report_id: int):
        with Session(engine) as db:
            row = db.get(ChangeReport, report_id)
            if row is None:
                raise HTTPException(404, "Change report not found")
            product = db.get(Product, row.product_id)
            context = dict(r=row, product=product, old=label(db.get(SDSVersion, row.old_version_id)),
                           new=label(db.get(SDSVersion, row.new_version_id)))
        return render(request, "report.html", **context)

    return router
