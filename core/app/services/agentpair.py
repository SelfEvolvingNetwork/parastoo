"""جفت‌سازی عامل پروژه با رمز «سرورساخته» + تأیید انسانی (v0.5.28).

همان الگوی اثبات‌شدهٔ پل ادمین (access-agent v0.8) اما با دانهٔ «پروژه»:
کاربر در صفحهٔ پروژه **متن دعوت بدون توکن** را کپی می‌کند و به عامل هوش
مصنوعی خودش می‌دهد؛ عامل آدرس دعوت (URL با شناسهٔ دعوت مخصوص همین
پروژه) را GET می‌کند و **سرور خودش رمز ۵ رقمی یک‌بارمصرف را می‌سازد و در
همان پاسخ می‌دهد** — عامل هیچ محاسبه‌ای نمی‌کند، فقط همان رمز را برای
کاربر چاپ می‌کند؛ کاربر همان رمز را در همان صفحه تأیید می‌کند؛ عامل توکن
`pst_` تازه (مخصوص همین پروژه) را برمی‌دارد. سرور از همان لحظه می‌داند
این عامل پای کدام کاربر/پروژه در میان است و همهٔ فراخوانی‌هایش در audit
پل ثبت می‌شود.

قرارداد امنیتی (آینهٔ پل ادمین):
1. هیچ رازی از چت عبور نمی‌کند. متن دعوت فقط مسیر و شناسهٔ دعوت دارد —
   لورفتنش بی‌خطر است؛ اعتماد با تأیید انسانیِ صاحب پروژه ساخته می‌شود.
2. روی دیسک فقط هش (رمز، راز ادعا و شناسهٔ دعوت) می‌ماند
   (dev/agent-pairing.json و dev/agent-invites.json، chmod 600)؛ audit هرگز
   مقدار رمز یا iid را نمی‌نویسد.
3. سقف‌ها: نرخ هر IP = ۵ در ۵ دقیقه؛ ادعای هم‌زمان هر IP = ۲ و هر
   پروژه = ۴؛ پنجرهٔ تأیید ۱۵ دقیقه (v0.5.28 — رمز حالا از مسیر اپ/چت
   غیرهم‌زمان به کاربر می‌رسد)؛ برداشت یک‌بارمصرف تا ۳۰ دقیقه پس از
   تأیید. تطبیق در تأیید: قدیمی‌ترین ادعای زندهٔ «همان پروژه».
4. یک دعوت = یک عامل: شناسهٔ دعوت با نخستین تأیید مصرف می‌شود؛ رندر
   تازهٔ صفحهٔ پروژه دعوت تازه می‌سازد و دعوت‌های قبلی همان پروژه را
   می‌کُشد (تازه‌ترین دعوت، دعوت زندهٔ پروژه است).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

from ..config import settings

APPROVE_WINDOW_S = 900     # مهلت تأیید کاربر — v0.5.28: ۱۵ دقیقه (آینهٔ پل v0.8)
PICKUP_WINDOW_S = 1800     # مهلت برداشت توکن پس از تأیید
PENDING_MAX_PER_PROJECT = 4
PENDING_PER_IP = 2
RATE_WINDOW_S = 300
RATE_MAX = 5

# ---------- دعوت‌نامهٔ پروژه-مند (v0.5.28 — آینهٔ پل v0.8) ----------
# شناسهٔ دعوت (iid) یک «توانایی» است: ۹۶ بیت تصادفی، فقط هش روی دیسک،
# raw فقط در همان رندر صفحه/پاسخی که آن را ساخته چاپ می‌شود. TTL ۲۴ ساعت؛
# سقف جهانی ۲۵۶ دعوت زنده؛ رندر تازه = دعوت‌های قبلی همان پروژه می‌میرند.

INVITE_TTL_S = 24 * 3600
INVITES_MAX = 256

_LOCK = threading.Lock()
_RATE: dict[str, list[float]] = {}


# ---------- ذخیرهٔ ادعاها و دعوت‌ها (فقط هش) ----------

def _claims_path() -> Path:
    d = Path(settings.DATA_DIR) / "dev"
    d.mkdir(parents=True, exist_ok=True)
    return d / "agent-pairing.json"


def _invites_path() -> Path:
    d = Path(settings.DATA_DIR) / "dev"
    d.mkdir(parents=True, exist_ok=True)
    return d / "agent-invites.json"


def _load_json_list(path: Path, key: str) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return [c for c in data[key] if isinstance(c, dict)]
    except Exception:
        pass
    return []


def _save_json_list(path: Path, key: str, rows: list[dict]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({key: rows}, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except Exception:
        pass


def _load() -> list[dict]:
    return _load_json_list(_claims_path(), "claims")


def _save(claims: list[dict]) -> None:
    _save_json_list(_claims_path(), "claims", claims)


def _load_invites() -> list[dict]:
    return _load_json_list(_invites_path(), "invites")


def _save_invites(invites: list[dict]) -> None:
    _save_json_list(_invites_path(), "invites", invites)


def _sweep_invites(invites: list[dict], now: float | None = None) -> list[dict]:
    """دعوت‌های مصرف‌شده/منقضی را می‌ریزد — فقط زنده‌ها می‌مانند."""
    now = time.time() if now is None else now
    return [i for i in invites
            if not i.get("used") and i.get("expires_ts", 0.0) > now]


def _sweep(claims: list[dict], now: float | None = None) -> list[dict]:
    """ادعاهای مرده را می‌ریزد: مصرف‌شده‌ها فوری؛ بقیه تا پایان پنجرهٔ برداشت."""
    now = time.time() if now is None else now
    keep: list[dict] = []
    for c in claims:
        if c.get("consumed"):
            continue
        if c.get("approved"):
            deadline = c.get("approved_ts", 0.0) + PICKUP_WINDOW_S
        else:
            deadline = (c.get("created_ts", 0.0)
                        + (c.get("approve_window_s") or APPROVE_WINDOW_S)
                        + PICKUP_WINDOW_S)
        if now <= deadline:
            keep.append(c)
    return keep


def _pending(c: dict, now: float) -> bool:
    return (not c.get("approved") and not c.get("consumed")
            and c.get("created_ts", 0.0)
            + (c.get("approve_window_s") or APPROVE_WINDOW_S) > now)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---------- audit (جدا از پل توکن‌دار؛ بدون هیچ مقدار رمز/iid) ----------

def audit_pair(action: str, detail: str = "", ip: str = "", ok: bool = True) -> None:
    try:
        d = Path(settings.DATA_DIR) / "dev"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "agentpair-audit.jsonl"
        rec = {"ts": datetime.now().isoformat(timespec="seconds"),
               "action": action, "ok": ok, "ip": ip,
               "detail": (detail or "")[:200]}
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # audit هرگز نباید خودش خطا بدهد


# ---------- چرخهٔ جفت‌سازی (رمز سرورساخته — v0.5.28) ----------

def mint_invite(project_id: int) -> tuple[str, dict]:
    """ساخت دعوت پروژه-مند — raw فقط در همین رندر/پاسخ برمی‌گردد.

    رندر تازهٔ صفحهٔ پروژه دعوت‌های قبلیِ همان پروژه را می‌کُشد: همیشه فقط
    تازه‌ترین دعوتِ هر پروژه زنده است (متنِ روی صفحه = دعوتِ معتبر)."""
    now = time.time()
    raw = "pinv_" + secrets.token_hex(12)
    rec = {
        "iid_sha": _sha(raw),
        "project_id": int(project_id),
        "created_ts": now,
        "expires_ts": now + INVITE_TTL_S,
        "used": False,
    }
    with _LOCK:
        invites = _sweep_invites(_load_invites(), now)
        invites = [i for i in invites if i.get("project_id") != int(project_id)]
        if len(invites) >= INVITES_MAX:
            invites = invites[-INVITES_MAX + 1:]
        invites.append(rec)
        _save_invites(invites)
    return raw, rec


def _find_live_invite(project_id: int, raw_iid: str, now: float | None = None):
    """دعوت زندهٔ منطبق با همین پروژه — هش + مقایسهٔ زمان-ثابت؛ وگرنه None."""
    if not raw_iid:
        return None
    now = time.time() if now is None else now
    target = _sha(raw_iid)
    for i in _sweep_invites(_load_invites(), now):
        if i.get("project_id") == int(project_id) \
                and hmac.compare_digest(str(i.get("iid_sha", "")), target):
            return i
    return None


def _use_invite(iid_sha: str) -> None:
    """مصرف دعوت با نخستین تأیید (یک دعوت = یک عامل). فقط هش می‌گیرد."""
    now = time.time()
    invites = _sweep_invites(_load_invites(), now)
    for i in invites:
        if hmac.compare_digest(str(i.get("iid_sha", "")), str(iid_sha)):
            i["used"] = True
            i["used_ts"] = now
    _save_invites(invites)


def mint_server_claim(project_id: int, raw_iid: str, ip: str = "") -> dict:
    """قلب v0.5.28 — رمز ۵ رقمی را «سرور» می‌سازد و به عامل می‌دهد تا فقط چاپش کند.

    ورودی توانایی است، نه راز چت: شناسهٔ دعوتی که کاربر در متن دعوت به
    عامل داده است. خروجی: رمز raw (فقط در همین پاسخ)، claim_id و
    claim_secret. فقط هش‌ها روی دیسک می‌مانند. سقف‌ها همان v0.5.27 است."""
    global _RATE
    now = time.time()
    with _LOCK:
        invite = _find_live_invite(project_id, raw_iid, now)
        if invite is None:
            return {"ok": False, "status": 404, "error": "unknown_invite",
                    "detail": "invite unknown, expired or already used — ask your "
                              "user for a fresh invite text from the project page"}
        # جاروی دیکشنری نرخ — با آدرس‌های یکتای بسیار، حافظه بی‌کران نگردد
        if len(_RATE) > 512:
            cutoff = now - RATE_WINDOW_S
            _RATE = {k: [t for t in v if t > cutoff] for k, v in _RATE.items()}
            _RATE = {k: v for k, v in _RATE.items() if v}
        window = [t for t in _RATE.get(ip, []) if t > now - RATE_WINDOW_S]
        if len(window) >= RATE_MAX:
            return {"ok": False, "status": 429, "error": "rate_limited",
                    "detail": "too many pairing codes from this address — wait a few minutes"}
        claims = _sweep(_load(), now)
        pending = [c for c in claims if _pending(c, now)]
        if sum(1 for c in pending if c.get("project_id") == project_id) \
                >= PENDING_MAX_PER_PROJECT:
            return {"ok": False, "status": 429, "error": "busy",
                    "detail": "too many pending claims for this project — try again later"}
        if sum(1 for c in pending if c.get("ip") == ip) >= PENDING_PER_IP:
            return {"ok": False, "status": 429, "error": "busy",
                    "detail": "too many pending claims from this address"}
        # رمز سرورساخته — توزیع یکنواخت روی ۱۰۰٬۰۰۰ حالت؛ نه محاسبهٔ عامل،
        # نه سوگیری زبانی، نه فهرست رمز ضعیف. صفرهای ابتدایی حفظ می‌شوند.
        code = f"{secrets.randbelow(100000):05d}"
        cid = "apc_" + secrets.token_hex(12)
        secret = "apcsk_" + secrets.token_hex(24)
        claims.append({
            "id": cid,
            "secret_sha": _sha(secret),
            "code_sha": _sha(code),
            "iid_sha": str(invite.get("iid_sha", "")),
            "project_id": int(project_id),
            "ip": ip,
            "created_ts": now,
            "approve_window_s": APPROVE_WINDOW_S,
            "approved": False,
            "approved_ts": 0.0,
            "consumed": False,
        })
        _save(claims)
        window.append(now)
        _RATE[ip] = window
    return {"ok": True, "code": code, "claim_id": cid, "claim_secret": secret,
            "approve_window_s": APPROVE_WINDOW_S,
            "pickup_window_s": PICKUP_WINDOW_S}


def approve(project_id: int, code: str, ip: str = "") -> dict:
    """تأیید انسانی — فقط قدیمی‌ترین ادعای زندهٔ «همان پروژه» تأیید می‌شود.

    دانه‌بندی پروژه یعنی تأیید صاحب پروژهٔ A هرگز ادعای پروژهٔ B را باز
    نمی‌کند (حتی با رمز یکسان). مقدار رمز هرگز ذخیره یا لاگ نمی‌شود.
    v0.5.28: با همین تأیید، دعوتِ ادعا هم مصرف می‌شود (یک دعوت = یک عامل)."""
    code = str(code or "").strip()
    if len(code) != 5 or not (code.isascii() and code.isdigit()):
        return {"ok": False, "status": 400, "error": "bad_code",
                "detail": "code must be exactly 5 ASCII digits"}
    target = _sha(code)
    now = time.time()
    with _LOCK:
        claims = _sweep(_load(), now)
        cands = [c for c in claims
                 if _pending(c, now)
                 and c.get("project_id") == int(project_id)
                 and hmac.compare_digest(str(c.get("code_sha", "")), target)]
        if not cands:
            _save(claims)
            return {"ok": False, "status": 404, "error": "no_pending_claim",
                    "detail": "no pending claim matches this code for this "
                              "project (wrong, expired or already approved)"}
        oldest = min(cands, key=lambda c: c.get("created_ts", 0.0))
        oldest["approved"] = True
        oldest["approved_ts"] = now
        _save(claims)
        iid_sha = str(oldest.get("iid_sha", ""))
        if iid_sha:
            _use_invite(iid_sha)  # یک دعوت = یک عامل؛ مصرف با تأیید
    return {"ok": True, "claim_id": oldest.get("id", ""),
            "pickup_window_s": PICKUP_WINDOW_S}


def collect(db, claim_id: str, claim_secret: str) -> dict:
    """برداشت توکن pst_ تازه — فقط یک بار، فقط با راز ادعا.

    وضعیت‌ها: pending / approved (+pst_ توکن پروژه) / expired / unknown."""
    now = time.time()
    with _LOCK:
        claims = _sweep(_load(), now)
        rec = next((c for c in claims if c.get("id") == claim_id), None)
        if rec is None or rec.get("consumed"):
            _save(claims)
            return {"ok": True, "status": "unknown"}
        if not hmac.compare_digest(_sha(claim_secret or ""),
                                   str(rec.get("secret_sha", ""))):
            _save(claims)
            return {"ok": True, "status": "unknown"}
        if rec.get("approved"):
            rec["consumed"] = True
            _save(claims)
            # توکن فقط همین‌جا ساخته می‌شود — دانهٔ آن همین پروژه است
            from ..models import Project
            project = db.get(Project, rec.get("project_id"))
            if project is None:
                return {"ok": True, "status": "unknown"}
            from . import bridge as bsvc
            raw, row = bsvc.mint(db, project, "عامل جفت‌شده (تأیید کاربر)")
            return {"ok": True, "status": "approved", "agent_token": raw,
                    "token_prefix": row.prefix, "project": project.slug,
                    "note": "project-scoped pst_ token — same realm as the "
                            "panel's Agent Bridge; revoke it from the project page"}
        if now > rec.get("created_ts", 0.0) \
                + (rec.get("approve_window_s") or APPROVE_WINDOW_S):
            _save(claims)
            return {"ok": True, "status": "expired"}
        _save(claims)
        remaining = int(rec.get("created_ts", 0.0)
                        + (rec.get("approve_window_s") or APPROVE_WINDOW_S) - now) + 1
        return {"ok": True, "status": "pending", "retry_after_s": max(0, remaining)}


# ---------- متن دعوت (بی‌رمز — لورفتنش بی‌خطر است) ----------

def invite_text(slug: str, panel_url: str, iid: str) -> str:
    """متن آمادهٔ کپی برای عامل هوش مصنوعیِ کاربر — هیچ توکنی در آن نیست.

    v0.5.28: آدرس دعوت، شناسهٔ دعوت مخصوص همین پروژه را حمل می‌کند و
    رمز ۵ رقمی را «سرور» در پاسخ GET می‌سازد — عامل هیچ محاسبه‌ای
    نمی‌کند؛ فقط همان رمز را برای کاربر چاپ می‌کند و کاربر در صفحهٔ
    پروژه تأییدش می‌کند."""
    return (
        "---- PARASTOO AGENT INVITE — copy to your AI agent ----\n"
        "This invite was generated by the Parastoo panel at your user's request.\n"
        f"It invites you to act as the development agent of ONE project: '{slug}'.\n"
        "No token or secret is included on purpose — nothing sensitive passes through chat.\n"
        "\n"
        f"guide (read first): {panel_url}/docs/agent\n"
        "invite URL (GET it exactly as-is):\n"
        f"{panel_url}/api/agent/onboarding?project={slug}&iid={iid}\n"
        "\n"
        "The server's answer to that GET contains:\n"
        "  a) a one-time 5-digit code the SERVER generated — you compute\n"
        "     nothing and invent nothing; print that exact code for your user,\n"
        "     unchanged.\n"
        "  b) claim_id + claim_secret — keep both to yourself.\n"
        "Your user relays the printed code back and confirms it on the project\n"
        f"page ('تأیید عامل هوش مصنوعی') — within {APPROVE_WINDOW_S // 60} minutes of the\n"
        "code being issued; if it expires, GET the invite URL again for a fresh one.\n"
        f"Then POST {panel_url}/api/agent/claim/poll  body: "
        "{\"claim_id\": \"...\", \"claim_secret\": \"...\"}\n"
        "   statuses: pending (retry) / approved (a project-scoped pst_ token comes "
        "with the response; pickup is one-time, available for 30 min after approval)\n"
        "Use that token as `Authorization: Bearer pst_...` on the Agent Bridge\n"
        f"   API ({panel_url}/api/bridge/*) and git remote — the contract and red\n"
        "   lines live in the guide above. It is scoped to this project only,\n"
        "   instantly revocable by your user, and every call is audited.\n"
        "---- END INVITE ----"
    )
