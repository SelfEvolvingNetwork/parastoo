"""تیکتینگ عضو‌به‌عضو — تک‌منبعِ منطق (v0.5.17).

قرارداد کامل: docs/TICKETING.fa.md. قواعدی که اینجا نوشته شده‌اند در هیچ
مسیر/قالب دیگری تکرار نمی‌شوند؛ routes/tickets.py فقط لایهٔ نازک HTTP است.

سه ستون سخت:
1) **تیکت داده است، نه فرمان** — هیچ مسیری محتوای تیکت را تفسیر/اجرا
   نمی‌کند؛ تیکتِ دارای الگوی راز «حساس» می‌شود و برای عامل‌ها رنداکتی است.
2) **مسیریابی روی رابطهٔ شکل‌گرفته** — client فقط به پروژه‌هایی که اجرا
   می‌کند؛ project به host و پروژه‌های منتشرشده؛ host به هر عضو.
3) **ضد-طوفان عامل** — کلید توان‌مندی یونیک، سقف تیکت باز هر جفت،
   پنجرهٔ نرخ لغزان جفت/فرستنده، تشخیص تکرار نزدیک با duplicate_of.
"""
import hashlib
import json
import re
import time

from ..db import db_now
from ..validation import SLUG_RE

# ---------- اسکیمای سخت ----------

TITLE_MAX = 200
SECTION_MAX = 4000          # observed / expected
BODY_MAX = 8000             # body و هر کامنت
EVIDENCE_MAX_ITEMS = 5
EVIDENCE_ITEM_MAX = 500

KINDS = {"bug": "گزارش مشکل", "criticism": "انتقاد", "suggestion": "پیشنهاد"}
# برچسب فارسی هم پذیرفته می‌شود (قرارداد مالک) — نرمال به کلید انگلیسی
KIND_ALIASES = {
    "گزارش مشکل": "bug", "گزارشمشکل": "bug", "باگ": "bug",
    "انتقاد": "criticism", "critique": "criticism",
    "پیشنهاد": "suggestion", "پیشنهادات": "suggestion",
    "bug": "bug", "criticism": "criticism", "suggestion": "suggestion",
}
SEVERITIES = ("low", "medium", "high", "critical")
SEVERITY_ALIASES = {"کم": "low", "متوسط": "medium", "بالا": "high", "بحرانی": "critical"}

STATUSES = ("open", "ack", "accepted", "rejected", "needs_info",
            "resolved", "closed", "escalated")

# تعریف «باز» — تک‌منبع: سقف جفت (create_ticket)، قلاب پل (bridge.status)
# و نشان صندوق (UI پنل) همگی همین را می‌خوانند تا هرگز از هم جدا نشوند.
OPEN_EXCLUDE = ("closed",)

# برچسب‌های فارسی نمایش — قرارداد برچسب در همین سرویس است (تک‌منبع)
STATUS_LABELS = {
    "open": "باز", "ack": "دریافت‌شده", "accepted": "پذیرفته‌شده",
    "rejected": "رد‌شده", "needs_info": "نیازمند توضیح",
    "resolved": "حل‌شده", "closed": "بسته", "escalated": "بن‌بست (انسانی)",
}
SEVERITY_LABELS = {"low": "کم", "medium": "متوسط", "high": "بالا", "critical": "بحرانی"}
EVENT_LABELS = {"create": "ایجاد", "comment": "کامنت", "status": "گذار وضعیت",
                "reopen": "بازگشایی", "escalate": "بنی‌سرگشت"}

# ---------- ضد-طوفان ----------

MAX_OPEN_PER_PAIR = 5                 # تیکت بازِ هم‌زمان هر جفت فرستنده→مقصد
PAIR_RATE = (10, 3600)                # ۱۰ تیکت تازه در ساعت برای هر جفت
SENDER_RATE = (30, 3600)              # ۳۰ تیکت تازه در ساعت برای هر فرستنده

# ---------- حساس‌سنجی (تیکت = داده، نه فرمان) ----------

# الگوی کلید-مقدار — درس تیکت ۱ (مثبت‌کاذب): «مقدار» باید شبیه راز باشد نه نثر.
# شرط: ۱۰+ نویسهٔ ASCII از خانوادهٔ راز با حداقل یک رقم؛ نثر فارسی/انگلیسی
# («توکن = همهٔ اندپوینت‌ها») تطبیق نمی‌گیرد.
_KV_SECRET = (
    r"(?:رمز|پسورد|گذرواژه|توکن|secret|token|password|passwd)"
    r"\s*[:=]\s*[\"']?"
    r"(?=[A-Za-z0-9_+/=-]{10,})"
    r"(?=[A-Za-z0-9_+/=-]*[0-9])"
    r"[A-Za-z0-9_+/=-]{10,}"
)

_SENSITIVE_RE = re.compile(
    r"(pst_[A-Za-z0-9_-]{8,})"                                  # توکن پل
    r"|(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"     # توکن گیت‌هاب
    r"|(AKIA[0-9A-Z]{16})"                                      # کلید AWS
    r"|(-----BEGIN [A-Z ]*PRIVATE KEY-----)"                    # کلید خصوصی
    r"|(eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,})"             # JWT
    r"|(" + _KV_SECRET + r")",                                  # کلید: مقدارِ شبیه‌راز
    re.IGNORECASE,
)
REDACTED = "[محتوای محرمانه — بررسی انسانی لازم است]"

# پذیرش گذار accepted→resolved فقط با سندِ شامل شناسهٔ کامیت (۷ تا ۴۰ هگز)
COMMIT_RE = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)

# ---------- ماشین حالت ----------
# کلید: وضعیت فعلی؛ مقدار: وضعیت‌های مجاز برای نقش مربوطه.
# گذارِ مجاز = «تقاطع» نقش فراخواننده با وضعیت فعلی — هیچ گذار دیگری نه.

RECEIVER_MOVES = {
    "open": {"ack"},
    "ack": {"accepted", "rejected", "needs_info"},
    "needs_info": {"accepted", "rejected"},
    "accepted": {"resolved"},   # فقط با سند بیرونی (در change_status گارد می‌شود)
}
SENDER_MOVES = {
    "rejected": {"open"},          # reopen (سقف ۱ — عبور از سقف → escalated)
    "resolved": {"open", "closed"},  # reopen یا پذیرش نتیجه
}
ADMIN_MOVES = {"escalated": {"closed", "ack"}}   # فقط ادمین انسانی — بن‌بست برای انسان


class TicketError(Exception):
    """خطای بامعنای تیکتینگ — کدِ ثابت برای مسیر HTTP."""

    def __init__(self, code: str, message: str, extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra or {}


# ---------- پنجرهٔ نرخ لغزان در-حافظه (همان الگوی پل عامل) ----------

_windows: dict[tuple, list[float]] = {}


def _allow(key: tuple, limit: int, window: int) -> bool:
    now = time.monotonic()
    lst = [t for t in _windows.get(key, []) if now - t < window]
    ok = len(lst) < limit
    if ok:
        lst.append(now)
    _windows[key] = lst
    return ok


# ---------- عضوها و آدرس‌دهی ----------

def resolve_member(db, ref: str):
    """رشتهٔ آدرس → (type, id) یا None. قالب: user:<id> یا project:<slug>."""
    ref = (ref or "").strip()
    if ref.startswith("user:"):
        tail = ref[5:]
        if not tail.isdigit():
            return None
        from ..models import User
        u = db.get(User, int(tail))
        return ("user", u.id) if u and u.is_active else None
    if ref.startswith("project:"):
        slug = ref[8:]
        if not SLUG_RE.match(slug):
            return None
        from ..models import Project
        p = db.query(Project).filter(Project.slug == slug).first()
        return ("project", p.id) if p else None
    return None


def member_ref(db, mtype: str, mid: int) -> str:
    if mtype == "user":
        return f"user:{mid}"
    from ..models import Project
    p = db.get(Project, mid)
    return f"project:{p.slug}" if p else f"project:{mid}"


def member_label(db, mtype: str, mid: int) -> str:
    """نمایش فارسی عضو — برای UI و گزارش."""
    if mtype == "user":
        from ..models import User
        u = db.get(User, mid)
        if u is None:
            return f"کاربر {mid}"
        if u.is_admin:
            return f"میزبان ({u.name or u.email})"
        return f"کاربر ({u.name or u.email})"
    from ..models import Project
    p = db.get(Project, mid)
    return f"پروژه ({p.name})" if p else f"پروژه {mid}"


def _client_runs_project(db, user_id: int, project_id: int) -> bool:
    """آیا این کاربر «ایمیجِ» این پروژه را اجرا می‌کند؟
    دو رابطهٔ معتبر: نصب مارکت (پروژهٔ او با source_project_id) یا
    سایتِ منتشرِ او روی پروژه. مسیریابی فقط از این روابط عبور می‌کند."""
    from ..models import Project, Site
    installed = (db.query(Project)
                 .filter(Project.owner_id == user_id,
                         Project.source_project_id == project_id)
                 .first())
    if installed:
        return True
    site = (db.query(Site)
            .filter(Site.owner_id == user_id, Site.project_id == project_id)
            .first())
    return site is not None


def routing_error(db, sender: tuple, receiver: tuple) -> str | None:
    """ماتریس مسیریابی — یا None یعنی مجاز. sender/receiver: (type, id)."""
    st, sid = sender
    rt, rid = receiver
    if sender == receiver:
        return "self"
    if st == "project":
        if rt == "project":
            from ..models import Project
            other = db.get(Project, rid)
            if other is None:
                return "no_member"
            if not other.is_public:
                return "not_public"
            return None  # پروژه → پروژهٔ منتشرشدهٔ دیگر
        if rt == "user":
            from ..models import User
            u = db.get(User, rid)
            if u is None:
                return "no_member"
            return None if u.is_admin else "only_host"  # پروژه → فقط میزبان
        return "bad_target"
    if st == "user":
        from ..models import User
        sender_user = db.get(User, sid)
        if sender_user is None:
            return "no_sender"
        if sender_user.is_admin:
            return None  # میزبان → هر عضو
        if rt == "project":
            return None if _client_runs_project(db, sid, rid) else "not_running"
        return "client_user_only"  # کلاینت → کاربر ممنوع (فقط پروژه‌هایش)
    return "bad_sender"


# ---------- اسکیمای سخت ----------

def _clean_text(v, max_len: int) -> str:
    return str(v or "").strip()[:max_len]


def normalize_kind(v) -> str:
    k = str(v or "").strip().lower()
    return KIND_ALIASES.get(k) or KIND_ALIASES.get(str(v or "").strip()) or ""


def normalize_severity(v) -> str:
    s = str(v or "").strip().lower()
    return s if s in SEVERITIES else SEVERITY_ALIASES.get(str(v or "").strip(), "")


def schema_error(kind, severity, title, observed, expected, evidence) -> dict:
    """راستی‌آزمایی اسکمای سخت — یا {} یعنی سالم. خروجی: {field: error}."""
    if not normalize_kind(kind):
        return {"kind": "kind_invalid"}
    if not normalize_severity(severity):
        return {"severity": "severity_invalid"}
    title = _clean_text(title, TITLE_MAX + 1)
    if len(title.strip()) < 3:
        return {"title": "title_short"}
    if len(title) > TITLE_MAX:
        return {"title": "title_long"}
    if not _clean_text(observed, SECTION_MAX):
        return {"observed": "observed_required"}
    if len(str(observed or "")) > SECTION_MAX:
        return {"observed": "observed_long"}
    if not _clean_text(expected, SECTION_MAX):
        return {"expected": "expected_required"}
    if len(str(expected or "")) > SECTION_MAX:
        return {"expected": "expected_long"}
    if evidence is None:
        evidence = []
    if not isinstance(evidence, list) or len(evidence) > EVIDENCE_MAX_ITEMS:
        return {"evidence": "evidence_invalid"}
    for e in evidence:
        if not isinstance(e, str) or not e.strip() or len(e) > EVIDENCE_ITEM_MAX:
            return {"evidence": "evidence_invalid"}
    return {}


def contains_secret(text: str) -> bool:
    return bool(_SENSITIVE_RE.search(text or ""))


def scan_sensitive(title, observed, expected, body, evidence) -> bool:
    """الگوی راز در هر بخش تیکت → تیکت حساس (خط قرمز ۵-۱ قرارداد)."""
    blob = "\n".join([str(title or ""), str(observed or ""), str(expected or ""),
                      str(body or "")] + [str(e) for e in (evidence or [])])
    return contains_secret(blob)


def idempotency_key(sender: tuple, receiver: tuple, kind, title, observed,
                    expected, body) -> str:
    """هش قطعی محتوا — ارسال دوبارهٔ همان تیکت = همان کلید (ضد-طوفان)."""
    blob = "\x00".join([
        f"{sender[0]}:{sender[1]}", f"{receiver[0]}:{receiver[1]}",
        str(kind or ""), str(title or ""), str(observed or ""),
        str(expected or ""), str(body or ""),
    ])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _norm_title(t: str) -> str:
    return re.sub(r"\s+", " ", str(t or "").strip()).casefold()


# ---------- دید (رنداکتی برای عامل‌ها) ----------

def ticket_view(db, t, human_viewer: bool) -> dict:
    """نمای JSON تیکت برای فراخواننده؛ تیکت حساس برای عامل رنداکتی می‌شود."""
    redact = t.is_sensitive and not human_viewer
    return {
        "id": t.id,
        "kind": t.kind,
        "kind_fa": KINDS.get(t.kind, t.kind),
        "severity": t.severity,
        "severity_fa": SEVERITY_LABELS.get(t.severity, t.severity),
        "title": t.title,
        "status": t.status,
        "status_fa": STATUS_LABELS.get(t.status, t.status),
        "sender": member_ref(db, t.sender_type, t.sender_id),
        "sender_label": member_label(db, t.sender_type, t.sender_id),
        "receiver": member_ref(db, t.receiver_type, t.receiver_id),
        "receiver_label": member_label(db, t.receiver_type, t.receiver_id),
        "observed": REDACTED if redact else t.observed,
        "expected": REDACTED if redact else t.expected,
        "body": REDACTED if redact else t.body,
        "evidence": [REDACTED] * len(t.evidence()) if redact else t.evidence(),
        "is_sensitive": t.is_sensitive,
        "needs_human": t.is_sensitive,   # عامل باید بداند: دست نزن، انسان بیاید
        "reopen_count": t.reopen_count,
        "duplicate_of_id": t.duplicate_of_id,
        "resolution_evidence": (t.resolution_evidence or "")[:500],
        "created_at": t.created_at.isoformat(sep=" ") if t.created_at else None,
        "updated_at": t.updated_at.isoformat(sep=" ") if t.updated_at else None,
    }


def events_view(db, t, human_viewer: bool, limit: int = 200) -> list[dict]:
    """دفتر رخدادها؛ کامنتِ تیکت حساس هم برای عامل رنداکتی است."""
    from ..models import TicketEvent
    redact = t.is_sensitive and not human_viewer
    rows = (db.query(TicketEvent)
            .filter(TicketEvent.ticket_id == t.id)
            .order_by(TicketEvent.id.asc())
            .limit(max(1, min(limit, 500)))
            .all())
    out = []
    for ev in rows:
        d = ev.data()
        if redact and isinstance(d.get("body"), str):
            d["body"] = REDACTED
        out.append({
            "id": ev.id, "kind": ev.kind,
            "actor": member_ref(db, ev.actor_type, ev.actor_id)
                     if ev.actor_type in ("user", "project") else "system",
            "data": d,
            "created_at": ev.created_at.isoformat(sep=" ") if ev.created_at else None,
        })
    return out


# ---------- ایجاد تیکت ----------

def create_ticket(db, sender: tuple, human_sender: bool, payload: dict):
    """ایجاد تیکت با همهٔ گاردها. خروجی: (تیکت، duplicate: bool).
    هر نقض = TicketError با کد بامعنا."""
    from ..models import Ticket

    receiver = resolve_member(db, str(payload.get("to") or ""))
    if receiver is None:
        raise TicketError("bad_target", "آدرس مقصد معتبر نیست (user:<id> یا project:<slug>)")
    err = routing_error(db, sender, receiver)
    if err:
        raise TicketError("routing", f"مسیریابی تیکت مجاز نیست ({err})")

    kind = normalize_kind(payload.get("kind"))
    severity = normalize_severity(payload.get("severity"))
    title = str(payload.get("title") or "").strip()
    observed = str(payload.get("observed") or "").strip()
    expected = str(payload.get("expected") or "").strip()
    body = str(payload.get("body") or "").strip()
    evidence = payload.get("evidence") or []
    if body and len(body) > BODY_MAX:
        raise TicketError("schema", "شرح تکمیلی بلندتر از سقف ۸۰۰۰ است",
                          {"field": "body"})
    serr = schema_error(kind, severity, title, observed, expected, evidence)
    if serr:
        raise TicketError("schema", "اسکیمای تیکت کامل/معتبر نیست", serr)

    # ضد-طوفان: توان‌مندی، تکرار نزدیک، سقف‌ها
    ikey = idempotency_key(sender, receiver, kind, title, observed, expected, body)
    existing = db.query(Ticket).filter(Ticket.idempotency_key == ikey).first()
    if existing is not None:
        return existing, True   # همان تیکت — بدون ردیف جدید

    norm = _norm_title(title)
    dups = (db.query(Ticket)
            .filter(Ticket.sender_type == sender[0], Ticket.sender_id == sender[1],
                    Ticket.receiver_type == receiver[0],
                    Ticket.receiver_id == receiver[1], Ticket.kind == kind)
            .all())
    near = next((t for t in dups if _norm_title(t.title) == norm
                 and t.status != "closed"), None)
    if near is not None:
        raise TicketError("duplicate", "تیکت هم‌موضوعِ باز وجود دارد — به همان ارجاع بدهید",
                          {"duplicate_of": near.id})

    if not _allow(("pair", sender, receiver), PAIR_RATE[0], PAIR_RATE[1]):
        raise TicketError("rate_limited", "سقف نرخ این جفت پر شده — بعداً تلاش کنید")
    if not _allow(("sender", sender), SENDER_RATE[0], SENDER_RATE[1]):
        raise TicketError("rate_limited", "سقف نرخ فرستنده پر شده — بعداً تلاش کنید")
    open_count = (db.query(Ticket)
                  .filter(Ticket.sender_type == sender[0], Ticket.sender_id == sender[1],
                          Ticket.receiver_type == receiver[0],
                          Ticket.receiver_id == receiver[1],
                          Ticket.status.notin_(OPEN_EXCLUDE))
                  .count())
    if open_count >= MAX_OPEN_PER_PAIR:
        raise TicketError("rate_limited",
                          f"سقف {MAX_OPEN_PER_PAIR} تیکت باز برای این جفت پر است — ابتدا بازهای قدیمی را ببندید")

    sensitive = scan_sensitive(title, observed, expected, body, evidence)
    t = Ticket(
        sender_type=sender[0], sender_id=sender[1],
        receiver_type=receiver[0], receiver_id=receiver[1],
        kind=kind, severity=severity,
        title=title[:TITLE_MAX], observed=observed, expected=expected,
        body=body[:BODY_MAX],
        evidence_json=json.dumps([str(e)[:EVIDENCE_ITEM_MAX] for e in evidence],
                                 ensure_ascii=False),
        status="open", is_sensitive=sensitive,
        idempotency_key=ikey,
    )
    db.add(t)
    db.flush()
    _event(db, t, "create", sender, human_sender, {
        "kind": kind, "severity": severity, "title": title[:TITLE_MAX],
        "sensitive": sensitive,
    })
    db.commit()
    return t, False


def add_comment(db, t, actor: tuple, human_actor: bool, body: str):
    """کامنت طرف‌های تیکت — بدون تغییر وضعیت. تیکت حساسِ عامل‌دار: عامل
    نمی‌نویسد (انسان باید تصمیم بگیرد)، انسان‌ها آزادند."""
    from ..models import TicketEvent

    if not (_side_match(db, t, actor, "sender")
            or _side_match(db, t, actor, "receiver")):
        from ..models import User
        u = db.get(User, actor[1]) if actor[0] == "user" else None
        if not (u and u.is_admin):
            raise TicketError("not_party", "این تیکت از آنِ شما نیست")
    text = str(body or "").strip()
    if not text:
        raise TicketError("schema", "کامنت خالی است", {"field": "body"})
    if len(text) > BODY_MAX:
        raise TicketError("schema", "کامنت بلندتر از سقف ۸۰۰۰ است", {"field": "body"})
    if t.status == "closed":
        raise TicketError("transition", "تیکت بسته شده — برای ادامه reopen بدهید")
    if t.status == "escalated" and not human_actor:
        raise TicketError("transition", "تیکت در بن‌بست است — انسان تصمیم می‌گیرد")
    if t.is_sensitive and not human_actor:
        raise TicketError("needs_human", "تیکت حساس است — فقط انسان می‌تواند پاسخ بدهد")
    if contains_secret(text):
        t.is_sensitive = True   # راز تازه‌وارد → کل تیکت محرمانه می‌شود
        db.flush()
    ev = TicketEvent(ticket_id=t.id, actor_type=actor[0], actor_id=actor[1],
                     kind="comment",
                     data_json=json.dumps({"body": text}, ensure_ascii=False))
    db.add(ev)
    db.commit()
    return ev


# ---------- گذار وضعیت ----------

def _event(db, t, kind, actor, human_actor, data: dict):
    from ..models import TicketEvent
    at, aid = actor if actor else ("system", 0)
    db.add(TicketEvent(ticket_id=t.id, actor_type=at, actor_id=aid, kind=kind,
                       data_json=json.dumps(data, ensure_ascii=False)))


def user_project_ids(db, user_id: int) -> list[int]:
    """شناسهٔ پروژه‌های متعلق به کاربر — پایهٔ دیدِ «مالکِ پروژه».
    تیکتِ عامل، تیکتِ مالکِ آن هم هست: حساب‌پذیری انسانی بدون لو دادن بقیه."""
    from ..models import Project
    return [pid for (pid,) in db.query(Project.id)
            .filter(Project.owner_id == user_id).all()]


def _side_match(db, t, actor: tuple, which: str) -> bool:
    """فراخواننده «آن‌سو»ی تیکت است؟ تطبیق مستقیمِ عضو، یا مالکِ انسانیِ
    پروژهٔ همان‌سو (درس تیکت ۱ — مالکِ ppp تیکتِ عاملش را نمی‌دید)."""
    st, sid = ((t.sender_type, t.sender_id) if which == "sender"
               else (t.receiver_type, t.receiver_id))
    if actor == (st, sid):
        return True
    if actor[0] == "user" and st == "project":
        from ..models import Project
        p = db.get(Project, sid)
        return bool(p and p.owner_id == actor[1])
    return False


def _viewer_side(db, viewer: tuple, type_col, id_col):
    """شرط SQL «فراخواننده آن‌سو است» — مستقیم یا از طریق پروژه‌های متعلق."""
    from sqlalchemy import and_, or_
    if viewer[0] == "user":
        conds = [and_(type_col == "user", id_col == viewer[1])]
        pids = user_project_ids(db, viewer[1])
        if pids:
            conds.append(and_(type_col == "project", id_col.in_(pids)))
        return or_(*conds)
    return and_(type_col == viewer[0], id_col == viewer[1])


def open_counts(db, actor: tuple) -> dict:
    """شمار صندوق باز فراخواننده — تک‌منبع شمارش برای نشان ناوبری/داشبورد
    و قلاب پل (bridge.status). «باز» = هر وضعیت جز closed.
    فقط شمارش؛ هیچ محتوایی بیرون نمی‌رود."""
    from ..models import Ticket
    q = db.query(Ticket).filter(Ticket.status.notin_(OPEN_EXCLUDE))
    return {"inbox_open": q.filter(_viewer_side(db, actor, Ticket.receiver_type,
                                                Ticket.receiver_id)).count(),
            "sent_open": q.filter(_viewer_side(db, actor, Ticket.sender_type,
                                               Ticket.sender_id)).count()}


def allowed_moves(db, t, actor: tuple, is_admin: bool) -> set:
    """گذارهای مجازِ الان برای این فراخواننده — تک‌منبع ماشین حالت؛
    change_status و UI پنل هر دو از همین می‌خوانند تا نمایش هرگز با قاعدهٔ
    واقعی یکی نشود. گذارِ خاص reopen≤۱ در change_status تصمیم می‌گیرد؛
    دکمهٔ بازگشایی همیشه با همین مجوز نشان داده می‌شود."""
    allowed: set = set()
    if _side_match(db, t, actor, "receiver"):
        allowed |= RECEIVER_MOVES.get(t.status, set())
    if _side_match(db, t, actor, "sender"):
        allowed |= SENDER_MOVES.get(t.status, set())
    if is_admin and actor[0] == "user":
        allowed |= ADMIN_MOVES.get(t.status, set())
    return allowed


def change_status(db, t, actor: tuple, human_actor: bool, is_admin: bool,
                  new_status: str, evidence: str = ""):
    """گذار وضعیت طبق ماشین حالت؛ خروجی: (تیکت، event). نقض = TicketError.

    تقسیم کار (قرار مالک): پذیرش/رد حق درونی عضو است؛ ادمینِ غیرطرف فقط
    بن‌بست (escalated) را می‌بندد یا دوباره وارد چرخه می‌کند — هرگز به
    جای عضو accept/reject نمی‌کند.
    """
    new_status = str(new_status or "").strip().lower()
    if new_status not in STATUSES:
        raise TicketError("transition", f"وضعیت ناشناخته: {new_status}")

    is_sender = _side_match(db, t, actor, "sender")
    is_receiver = _side_match(db, t, actor, "receiver")
    if not (is_sender or is_receiver) and not (
            is_admin and actor[0] == "user"
            and new_status in ADMIN_MOVES.get(t.status, set())):
        # ادمینِ غیرطرف فقط روی بن‌بست حق دارد — کمک بررسی، نه داوری
        raise TicketError("not_party", "این تیکت از آنِ شما نیست")

    old = t.status
    allowed = allowed_moves(db, t, actor, is_admin)

    # گذار خاص — reopen فرستنده: سقف ۱؛ عبور از سقف = بن‌بست → escalated
    if is_sender and new_status == "open" and old in ("rejected", "resolved"):
        if t.reopen_count >= 1:
            t.status = "escalated"
            t.escalated_at = db_now()
            db.flush()
            ev = _event_and_commit(db, t, "escalate", actor, human_actor,
                                   {"reason": "reopen_limit", "from": old})
            return t, ev
        t.reopen_count += 1

    if new_status not in allowed:
        raise TicketError("transition",
                          f"گذار {old} → {new_status} برای نقش شما مجاز نیست")

    t.status = new_status
    if new_status == "escalated":
        t.escalated_at = db_now()

    extra = {"from": old, "to": new_status}
    if old == "accepted" and new_status == "resolved":
        # resolved فقط با سند بیرونی: شناسهٔ کامیت استقرار (قابل‌بررسی از /api/version)
        ev_text = str(evidence or "").strip()
        sha = COMMIT_RE.search(ev_text)
        if not sha:
            t.status = old  # گذار رد شد — وضعیت دست‌نخورده می‌ماند
            raise TicketError("transition",
                              "resolved نیازمند سند است: شناسهٔ کامیت استقرار (۷ تا ۴۰ نویسهٔ هگز)")
        t.resolution_evidence = ev_text[:EVIDENCE_ITEM_MAX * 2]
        extra["evidence"] = ev_text[:EVIDENCE_ITEM_MAX * 2]
        extra["verified_deployed"] = settings_deployed_commit() == sha.group(0).lower()

    db.flush()
    ev = _event_and_commit(db, t, "status", actor, human_actor, extra)
    return t, ev


def _event_and_commit(db, t, kind, actor, human_actor, data):
    from ..models import TicketEvent
    at, aid = actor if actor else ("system", 0)
    ev = TicketEvent(ticket_id=t.id, actor_type=at, actor_id=aid, kind=kind,
                     data_json=json.dumps(data, ensure_ascii=False))
    db.add(ev)
    db.commit()
    return ev


def settings_deployed_commit() -> str:
    """کامیت مستقر همین هسته — برای راستی‌آزمایی سندِ resolved."""
    from ..config import settings
    return (settings.DEPLOYED_COMMIT or "").strip().lower()


# ---------- صندوق‌ها ----------

def list_tickets(db, viewer: tuple, box: str, limit: int = 50):
    """inbox = رسید‌های فراخواننده؛ sent = ارسالی‌هایش. همیشه فیلترِ عضویت — IDOR-safe."""
    from ..models import Ticket
    q = db.query(Ticket)
    if box == "inbox":
        q = q.filter(_viewer_side(db, viewer, Ticket.receiver_type,
                                  Ticket.receiver_id))
    elif box == "sent":
        q = q.filter(_viewer_side(db, viewer, Ticket.sender_type,
                                  Ticket.sender_id))
    else:
        raise TicketError("bad_box", "box باید inbox یا sent باشد")
    return (q.order_by(Ticket.id.desc())
            .limit(max(1, min(limit, 100)))
            .all())


def ticket_for(db, viewer: tuple, is_admin: bool, ticket_id: int):
    """تیکت فقط برای طرف‌هایش (ادمین برای کمک بررسی). غریبه = 404 بامعنا."""
    from ..models import Ticket
    t = db.get(Ticket, ticket_id)
    if t is None:
        raise TicketError("not_found", "تیکت یافت نشد")
    party = (_side_match(db, t, viewer, "sender")
             or _side_match(db, t, viewer, "receiver"))
    if not party and not (is_admin and viewer[0] == "user"):
        raise TicketError("not_found", "تیکت یافت نشد")
    return t
