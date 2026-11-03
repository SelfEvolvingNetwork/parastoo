#!/usr/bin/env python3
"""Merge Docker Hub registry mirrors into /etc/docker/daemon.json — without breaking other keys.

Usage (install.sh calls this automatically; manual use also works):
    sudo python3 scripts/merge-docker-mirrors.py /etc/docker/daemon.json \
        https://docker.arvancloud.ir https://registry.docker.ir

- creates the file if it does not exist
- keeps unrelated keys (log-level, insecure-registries, ...)
- never duplicates mirrors; the user's existing mirrors keep their priority
- broken JSON -> error and exit code 1 (never silently destroys daemon.json)
"""
import json
import sys


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: merge-docker-mirrors.py /etc/docker/daemon.json MIRROR1 [MIRROR2 ...]",
              file=sys.stderr)
        return 2
    path, new_mirrors = sys.argv[1], sys.argv[2:]

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    except json.JSONDecodeError as e:
        print(f"error: {path} is not valid JSON ({e}). Fix it manually first.", file=sys.stderr)
        return 1

    if not isinstance(data, dict):
        print(f"error: {path} does not contain a JSON object.", file=sys.stderr)
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
        print(f"registry-mirrors added ({len(added)}): {', '.join(added)}")
    else:
        print("registry-mirrors already configured; no change needed.")
    print("now restart docker:  systemctl restart docker")
    return 0


if __name__ == "__main__":
    sys.exit(main())
