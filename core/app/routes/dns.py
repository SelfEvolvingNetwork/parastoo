"""DNS اختصاصی (v0.3.9): مدیریت نیم‌سرور و زون‌ها — UI ادمین + API JSON.

UI (/admin/dns*): با نشست ادمین + CSRF (admin_guard) — فهرست زون‌ها،
ساخت زون با پیش‌فرض‌های کامل، مدیریت رکوردها، حذف زون.
API (/api/dns/*): ادمینِ واردشده، یا عامل با X-Dev-Token — منطق گارد
همان کنسول توسعه است (routes/dev.py؛ همیشه روشن، بدون حالت — v0.5.29).
هر فراخوانی API در فایل audit پل عامل (dev/agent-audit.jsonl) ثبت می‌شود.

منطق واقعی در services/dnsops.py است (کلاینت REST پاوردی‌ان‌اس)؛ اینجا
فقط ورودی/خروجی وب + گارد + پیام‌ها.
"""
import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import domains
from ..config import settings
from ..db import SessionLocal, get_db
from ..deps import admin_guard, ctx, redirect, render, resolve_user
from ..services import agentops, dnsops

log = logging.getLogger("parastoo")
router = APIRouter()


def _session_user(request: Request):
    db = SessionLocal()
    try:
        return resolve_user(request, db)
    finally:
        db.close()


def _api_guard(request: Request) -> None:
    """ادمینِ واردشده یا عامل با X-Dev-Token — همیشه، بدون حالت (v0.5.29).
    بقیه → 401 (شفاف؛ وجود API پنهان نیست)."""
    user = _session_user(request)
    if user is not None and user.is_admin:
        return
    if settings.DEV_TOKEN:
        given = request.headers.get("X-Dev-Token") or request.query_params.get("token") or ""
        if given == settings.DEV_TOKEN:
            return
    raise HTTPException(status_code=401, detail="unauthorized")


def _dns_flash(e: Exception) -> str:
    if isinstance(e, dnsops.DnsUnavailable):
        return "dns_unavailable"
    if isinstance(e, dnsops.DnsError):
        return "dns_error" if e.status >= 500 else "dns_invalid"
    return "error"


# ============================================================
#  UI ادمین — /admin/dns
# ============================================================

def _ui_status(db: Session) -> dict:
    ns = []
    info = {}
    ok = False
    try:
        ok = dnsops.available()
        if ok:
            info = dnsops.server_info()
        ns = dnsops.nameservers(db)
    except Exception as e:
        log.warning("dns status failed: %r", e)
    return {"ok": ok, "info": info, "nameservers": ns,
            "server_ip": domains.server_ip(db), "base": domains.base(db)}


@router.get("/admin/dns")
def dns_page(request: Request, user=Depends(admin_guard), db: Session = Depends(get_db)):
    zones = dnsops.list_zones() if _ui_status(db)["ok"] else []
    return render("dns.html", ctx(request, user, zones=zones, status=_ui_status(db)))


@router.post("/admin/dns/create")
def dns_create(request: Request, name: str = Form(...),
               kind: str = Form("customer"),
               user=Depends(admin_guard), db: Session = Depends(get_db)):
    try:
        dnsops.provision_zone(name, db=db, kind=kind if kind in ("customer", "base") else None)
    except Exception as e:
        log.warning("dns create %s: %r", name[:100], e)
        return redirect("/admin/dns", m=_dns_flash(e))
    agentops.audit("dns", {"op": "create_zone", "name": name[:200], "kind": kind})
    return redirect(f"/admin/dns/{(name or '').strip().lower().rstrip('.')}", m="dns_created")


@router.get("/admin/dns/{zone}")
def dns_zone_page(request: Request, zone: str,
                  user=Depends(admin_guard), db: Session = Depends(get_db)):
    try:
        z = dnsops.get_zone(zone)
    except Exception as e:
        return redirect("/admin/dns", m=_dns_flash(e))
    return render("dns_zone.html", ctx(request, user, zone=z, status=_ui_status(db)))


@router.post("/admin/dns/{zone}/record")
def dns_record_add(request: Request, zone: str,
                   name: str = Form(""), type: str = Form(...),
                   content: str = Form(...), ttl: str = Form(""),
                   user=Depends(admin_guard), db: Session = Depends(get_db)):
    try:
        dnsops.upsert_record(zone, name, type, content, ttl=int(ttl) if ttl.strip().isdigit() else None)
    except Exception as e:
        log.warning("dns record %s %s: %r", zone[:100], type[:20], e)
        return redirect(f"/admin/dns/{zone}", m=_dns_flash(e))
    return redirect(f"/admin/dns/{zone}", m="dns_record_saved")


@router.post("/admin/dns/{zone}/record_delete")
def dns_record_delete(request: Request, zone: str,
                      name: str = Form(""), type: str = Form(...),
                      user=Depends(admin_guard), db: Session = Depends(get_db)):
    try:
        dnsops.delete_record(zone, name, type)
    except Exception as e:
        log.warning("dns record delete %s: %r", zone[:100], e)
        return redirect(f"/admin/dns/{zone}", m=_dns_flash(e))
    return redirect(f"/admin/dns/{zone}", m="dns_record_deleted")


@router.post("/admin/dns/{zone}/delete")
def dns_zone_delete(request: Request, zone: str,
                    user=Depends(admin_guard), db: Session = Depends(get_db)):
    try:
        dnsops.delete_zone(zone)
    except Exception as e:
        log.warning("dns zone delete %s: %r", zone[:100], e)
        return redirect("/admin/dns", m=_dns_flash(e))
    agentops.audit("dns", {"op": "delete_zone", "name": zone[:200]})
    return redirect("/admin/dns", m="dns_zone_deleted")


# ============================================================
#  API JSON — /api/dns (ادمین یا عامل با X-Dev-Token)
# ============================================================

class ZoneIn(BaseModel):
    name: str
    kind: str = "customer"   # customer | base


class RecordIn(BaseModel):
    name: str = ""           # خالی یا @ = ریشهٔ زون
    type: str
    content: str
    ttl: int | None = None


def _err_response(e: Exception):
    if isinstance(e, dnsops.DnsUnavailable):
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    if isinstance(e, dnsops.DnsError):
        return JSONResponse({"ok": False, "error": str(e)}, status_code=e.status)
    log.exception("dns api error")
    return JSONResponse({"ok": False, "error": "internal error"}, status_code=500)


@router.get("/api/dns/status")
def api_dns_status(request: Request, db: Session = Depends(get_db)):
    _api_guard(request)
    st = _ui_status(db)
    try:
        info = dnsops.server_info() if st["ok"] else {}
    except Exception as e:
        return _err_response(e)
    agentops.audit("dns", {"op": "status"})
    return JSONResponse({"ok": True, "available": st["ok"], "version": info.get("version", ""),
                         "nameservers": st["nameservers"], "server_ip": st["server_ip"],
                         "base": st["base"], "record_types": list(dnsops.RECORD_TYPES)})


@router.get("/api/dns/zones")
def api_dns_zones(request: Request):
    _api_guard(request)
    try:
        zones = dnsops.list_zones()
    except Exception as e:
        return _err_response(e)
    return JSONResponse({"ok": True, "zones": zones})


@router.post("/api/dns/zones")
def api_dns_zone_create(request: Request, body: ZoneIn):
    _api_guard(request)
    try:
        z = dnsops.provision_zone(body.name, kind=body.kind if body.kind in ("customer", "base") else None)
    except Exception as e:
        return _err_response(e)
    agentops.audit("dns", {"op": "create_zone", "name": body.name[:200], "kind": body.kind})
    return JSONResponse({"ok": True, "zone": z})


@router.get("/api/dns/zones/{zone}")
def api_dns_zone(request: Request, zone: str):
    _api_guard(request)
    try:
        z = dnsops.get_zone(zone)
    except Exception as e:
        return _err_response(e)
    return JSONResponse({"ok": True, "zone": z})


@router.post("/api/dns/zones/{zone}/records")
def api_dns_record_add(request: Request, zone: str, body: RecordIn):
    _api_guard(request)
    try:
        z = dnsops.upsert_record(zone, body.name, body.type, body.content, body.ttl)
    except Exception as e:
        return _err_response(e)
    agentops.audit("dns", {"op": "upsert_record", "zone": zone[:200],
                           "name": body.name[:200], "type": body.type[:20]})
    return JSONResponse({"ok": True, "zone": z})


@router.delete("/api/dns/zones/{zone}/records")
def api_dns_record_delete(request: Request, zone: str, name: str = "", type: str = ""):
    _api_guard(request)
    try:
        z = dnsops.delete_record(zone, name, type)
    except Exception as e:
        return _err_response(e)
    agentops.audit("dns", {"op": "delete_record", "zone": zone[:200],
                           "name": name[:200], "type": type[:20]})
    return JSONResponse({"ok": True, "zone": z})


@router.delete("/api/dns/zones/{zone}")
def api_dns_zone_delete(request: Request, zone: str):
    _api_guard(request)
    try:
        dnsops.delete_zone(zone)
    except Exception as e:
        return _err_response(e)
    agentops.audit("dns", {"op": "delete_zone", "name": zone[:200]})
    return JSONResponse({"ok": True})
