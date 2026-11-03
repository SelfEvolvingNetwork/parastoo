"""وب‌هوک گیتا: با هر push یک بیلد به صف اضافه می‌شود."""
import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Project
from ..workers import enqueue_build

router = APIRouter()


def _verify(project: Project, request: Request, body: bytes, query_secret: str | None) -> bool:
    secret = project.webhook_secret
    # 1) امضای هدر گیتا
    sig = request.headers.get("X-Gitea-Signature")
    if sig:
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)
    # 2) فالبک: توکن در کوئری (وقتی هدر امضا ست نشده)
    return bool(query_secret) and hmac.compare_digest(secret, query_secret)


@router.post("/api/hooks/gitea/{project_id}")
async def gitea_hook(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project is None:
        return {"ok": False, "error": "unknown project"}
    body = await request.body()
    if not _verify(project, request, body, request.query_params.get("s")):
        return {"ok": False, "error": "bad signature"}
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "bad payload"}

    sha = (payload.get("after") or "").strip()
    ref = payload.get("ref") or ""
    if sha and set(sha) != {"0"}:  # push واقعی (حذف برنچ نیست)
        # فقط پوش به برنچ‌های اصلی بیلد می‌گیرد (main/master)
        branch = ref.rsplit("/", 1)[-1] if ref else "main"
        if branch in ("main", "master", "trunk"):
            enqueue_build(project.id, sha)
    return {"ok": True}
