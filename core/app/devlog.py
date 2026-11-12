"""رینگ‌بافر لاگ در حافظه — زیرساخت گزارش زندهٔ کنسول توسعه (همیشه روشن؛ v0.5.29).

آخرین N رکورد لاگ این پروسه را نگه می‌دارد؛ منبع /api/dev/logs و بخش «خطاها»
در گزارش /api/dev/report. جدا از routes/dev.py تا هم مسیرهای HTTP به آن
وابسته باشند و هم main.py (لاگ خطاهای ۵۰۰) — بدون وابستگی مسیر به مسیر.
"""
import logging
from collections import deque

LOG_CAPACITY = 1000

_RING: deque[str] = deque(maxlen=LOG_CAPACITY)


class _RingHandler(logging.Handler):
    """آخرین N رکورد لاگ این پروسه در حافظه."""

    def __init__(self, capacity: int = LOG_CAPACITY):
        super().__init__()
        self.buf = _RING
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.buf.append(self.format(record))
        except Exception:
            pass


def attach() -> None:
    """اتصال idempotent به لاگرهای root/uvicorn — یک بار در import اول کافی است."""
    handler = None
    for name in ("", "parastoo", "uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        if not any(isinstance(h, _RingHandler) for h in lg.handlers):
            if handler is None:
                handler = _RingHandler()
            lg.addHandler(handler)
    logging.getLogger("parastoo").setLevel(logging.INFO)


def tail(n: int) -> list[str]:
    """آخرین n خط — تازه‌ترین آخر."""
    return list(_RING)[-n:]


def recent_errors(limit: int = 150) -> list[str]:
    return list(_RING)[-limit:]
