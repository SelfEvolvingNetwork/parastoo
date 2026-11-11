"""جفت‌سازی عامل پروژه با رمز عامل‌ساخته + تأیید انسانی (v0.5.27).

همان الگوی اثبات‌شدهٔ پل ادمین (access-agent v0.7.1) اما با دانهٔ «پروژه»:
کاربر در صفحهٔ پروژه **متن دعوت بدون توکن** را کپی می‌کند و به عامل هوش
مصنوعی خودش می‌دهد؛ عامل رمز ۵ رقمی می‌سازد و ادعا می‌کند؛ کاربر همان
رمز را در همان صفحه تأیید می‌کند؛ عامل توکن `pst_` تازه (مخصوص همین
پروژه) را برمی‌دارد. سرور از همان لحظه می‌داند این عامل پای کدام
کاربر/پروژه در میان است و همهٔ فراخوانی‌هایش در audit پل ثبت می‌شود.

قرارداد امنیتی (آینهٔ پل ادمین):
1. هیچ رازی از چت عبور نمی‌کند. متن دعوت فقط مسیر و شناسهٔ پروژه دارد —
   لورفتنش بی‌خطر است؛ اعتماد با تأیید انسانیِ صاحب پروژه ساخته می‌شود.
2. روی دیسک فقط هش (رمز و راز ادعا) می‌ماند (dev/agent-pairing.json،
   chmod 600)؛ audit هرگز مقدار رمز را نمی‌نویسد.
3. سقف‌ها: نرخ هر IP = ۵ در ۵ دقیقه؛ ادعای هم‌زمان هر IP = ۲ و هر
   پروژه = ۴؛ پنجرهٔ تأیید ۲ دقیقه؛ برداشت یک‌بارمصرف تا ۳۰ دقیقه پس
   از تأیید. تطبیق در تأیید: قدیمی‌ترین ادعای زندهٔ «همان پروژه».
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

APPROVE_WINDOW_S = 120     # مهلت تأیید کاربر — هم‌ارز پل ادمین (فرمان مالک)
PICKUP_WINDOW_S = 1800     # مهلت برداشت توکن پس از تأیید
PENDING_MAX_PER_PROJECT = 4
PENDING_PER_IP = 2
RATE_WINDOW_S = 300
RATE_MAX = 5

# رمزهای ضعیف — همان فهرست پل ادمین؛ عامل‌های زبانی این‌ها را «تصادفی»
# انتخاب می‌کنند و رد می‌شوند.
WEAK_CODES = frozenset({
    "00000", "11111", "22222", "33333", "44444", "55555", "66666",
    "77777", "88888", "99999", "12345", "54321", "01234", "43210",
    "98765", "56789", "67890", "11100", "00111", "10101", "01010",
    "12121", "12321", "11223", "10000", "01000", "00100", "00010",
    "00001", "20000", "30000", "90000", "13579", "24680", "11112", "22221",
})

_LOCK = threading.Lock()
_RATE: dict[str, list[float]] = {}


# ---------- ذخیرهٔ ادعاها (فقط هش) ----------

def _claims_path() -> Path:
    d = Path(settings.DATA_DIR) / "dev"
    d.mkdir(parents=True, exist_ok=True)
    return d / "agent-pairing.json"


def _load() -> list[dict]:
    try:
        data = json.loads(_claims_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("claims"), list):
            return [c for c in data["claims"] if isinstance(c, dict)]
    except Exception:
        pass
    return []


def _save(claims: list[dict]) -> None:
    p = _claims_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"claims": claims}, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    tmp.replace(p)
    try:
        p.chmod(0o600)
    except Exception:
        pass


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


# ---------- audit (جدا از پل توکن‌دار؛ بدون هیچ مقدار رمز) ----------

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


# ---------- چرخهٔ جفت‌سازی ----------

def mint_claim(project_id: int, code: str, ip: str = "") -> dict:
    """ثبت ادعای جفت‌سازی برای «همین پروژه» — رمز ۵ رقمیِ عامل‌ساخته."""
    global _RATE
    code = str(code or "").strip()
    if len(code) != 5 or not (code.isascii() and code.isdigit()):
        return {"ok": False, "status": 400, "error": "bad_code",
                "detail": "code must be exactly 5 ASCII digits (0-9)"}
    if code in WEAK_CODES:
        return {"ok": False, "status": 400, "error": "weak_code",
                "detail": "this code is too predictable — generate a fresh one "
                          "with secure randomness"}
    now = time.time()
    with _LOCK:
        claims = _sweep(_load(), now)
        # جاروی دیکشنری نرخ — با آدرس‌های یکتای بسیار، حافظه بی‌کران نگردد
        if len(_RATE) > 512:
            cutoff = now - RATE_WINDOW_S
            _RATE = {k: [t for t in v if t > cutoff] for k, v in _RATE.items()}
            _RATE = {k: v for k, v in _RATE.items() if v}
        window = [t for t in _RATE.get(ip, []) if t > now - RATE_WINDOW_S]
        if len(window) >= RATE_MAX:
            return {"ok": False, "status": 429, "error": "rate_limited",
                    "detail": "too many claims from this address — wait a few minutes"}
        pending = [c for c in claims if _pending(c, now)]
        if sum(1 for c in pending if c.get("project_id") == project_id) \
                >= PENDING_MAX_PER_PROJECT:
            return {"ok": False, "status": 429, "error": "busy",
                    "detail": "too many pending claims for this project — try again later"}
        if sum(1 for c in pending if c.get("ip") == ip) >= PENDING_PER_IP:
            return {"ok": False, "status": 429, "error": "busy",
                    "detail": "too many pending claims from this address"}
        cid = "apc_" + secrets.token_hex(12)
        secret = "apcsk_" + secrets.token_hex(24)
        claims.append({
            "id": cid,
            "secret_sha": _sha(secret),
            "code_sha": _sha(code),
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
    return {"ok": True, "claim_id": cid, "claim_secret": secret,
            "approve_window_s": APPROVE_WINDOW_S,
            "pickup_window_s": PICKUP_WINDOW_S}


def approve(project_id: int, code: str, ip: str = "") -> dict:
    """تأیید انسانی — فقط قدیمی‌ترین ادعای زندهٔ «همان پروژه» تأیید می‌شود.

    دانه‌بندی پروژه یعنی تأیید صاحب پروژهٔ A هرگز ادعای پروژهٔ B را باز
    نمی‌کند (حتی با رمز یکسان). مقدار رمز هرگز ذخیره یا لاگ نمی‌شود."""
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

def invite_text(slug: str, panel_url: str) -> str:
    """متن آمادهٔ کپی برای عامل هوش مصنوعیِ کاربر — هیچ توکنی در آن نیست.

    «متن هر کاربر متفاوت است»: شناسهٔ پروژه (slug) متن را منحصر به هر
    کاربر/پروژه می‌کند و سرور از روی همین می‌داند ادعا پای کدام پروژه است."""
    return (
        "---- PARASTOO AGENT INVITE — copy to your AI agent ----\n"
        "This invite was generated by the Parastoo panel at your user's request.\n"
        f"It invites you to act as the development agent of ONE project: '{slug}'.\n"
        "No token or secret is included on purpose — nothing sensitive passes through chat.\n"
        "\n"
        f"guide (read first): {panel_url}/docs/agent\n"
        f"panel: {panel_url}\n"
        f"project: {slug}\n"
        "\n"
        "Pairing (human-confirmed, no secrets in chat):\n"
        f"1) POST {panel_url}/api/agent/claim  body: {{\"project\": \"{slug}\", "
        "\"code\": \"<5-digit code YOU generate with secure randomness>\"}\n"
        "   (predictable codes like 12345 are rejected; response: claim_id + claim_secret)\n"
        "2) Print that same 5-digit code in chat and ask your user to confirm it\n"
        f"   on the project page, 'تأیید عامل هوش مصنوعی' — within 2 minutes.\n"
        f"3) POST {panel_url}/api/agent/claim/poll  body: "
        "{\"claim_id\": \"...\", \"claim_secret\": \"...\"}\n"
        "   statuses: pending (retry) / approved (a project-scoped pst_ token comes "
        "with the response; pickup is one-time, available for 30 min after approval)\n"
        "4) Use that token as `Authorization: Bearer pst_...` on the Agent Bridge\n"
        f"   API ({panel_url}/api/bridge/*) and git remote — the contract and red\n"
        "   lines live in the guide above. It is scoped to this project only,\n"
        "   instantly revocable by your user, and every call is audited.\n"
        "---- END INVITE ----"
    )
