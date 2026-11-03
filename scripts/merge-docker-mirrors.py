#!/usr/bin/env python3
"""ادغام آینه‌های رجیستری Docker Hub در /etc/docker/daemon.json — بدون خرابکردن کلیدهای دیگر.

استفاده (install.sh خودش صدا می‌زند؛ دستی هم می‌شود):
    sudo python3 scripts/merge-docker-mirrors.py /etc/docker/daemon.json \
        https://docker.arvancloud.ir https://registry.docker.ir

- اگر فایل وجود نداشت ساخته می‌شود
- اگر کلیدهای دیگری دارد (log-level، insecure-registries و ...) حفظ می‌شوند
- آینه‌های تکراری اضافه نمی‌شوند؛ آینه‌های قبلی کاربر جلوتر می‌مانند
- JSON خراب → خطا و خروج ۱ (نمی‌گذارد بی‌سروصدا daemon.json را نابود کند)
"""
import json
import sys


def main() -> int:
    if len(sys.argv) < 3:
        print("استفاده: merge-docker-mirrors.py /etc/docker/daemon.json MIRROR1 [MIRROR2 ...]",
              file=sys.stderr)
        return 2
    path, new_mirrors = sys.argv[1], sys.argv[2:]

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    except json.JSONDecodeError as e:
        print(f"خطا: {path} معتبر نیست ({e}). آن را دستی اصلاح کنید.", file=sys.stderr)
        return 1

    if not isinstance(data, dict):
        print(f"خطا: محتوای {path} یک شیء JSON نیست.", file=sys.stderr)
        return 1

    mirrors = [m for m in data.get("registry-mirrors", []) if isinstance(m, str)]
    added = []
    for m in new_mirrors:
        m = m.strip().rstrip("/")
        if m and m not in mirrors:
            mirrors.append(m)
            added.append(m)
    data["registry-mirrors"] = mirrors

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    if added:
        print(f"registry-mirrors اضافه شد ({len(added)}): {', '.join(added)}")
    else:
        print("registry-mirrors از قبل تنظیم بود؛ تغییری لازم نبود.")
    print("حالا داکر را ری‌استارت کنید:  systemctl restart docker")
    return 0


if __name__ == "__main__":
    sys.exit(main())
