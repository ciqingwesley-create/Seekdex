"""Local history inventory. Reports locations, never credential values or private text."""
from pathlib import Path
import argparse
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / ".verification/git-audit.json")
    args = parser.parse_args()
    tracked = set(git("ls-files", "-z").decode("utf8").split("\0")) - {""}
    objects = git("rev-list", "--objects", "--all").decode("utf8").splitlines()
    inventory = subprocess.check_output(["git", "cat-file", "--batch-check=%(objecttype) %(objectname) %(objectsize) %(rest)"],
                                       input=("\n".join(objects) + "\n").encode(), cwd=ROOT).decode("utf8").splitlines()
    private = re.compile(r"C:[\\/]+(?:Users|Projects)[\\/]|(?:E|K):[\\/]+(?:RAW|图像|My digital photograph)", re.I)
    credentials = re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
    keyword = re.compile(r"api.?key|password|secret|oauth.?token|access.?token|refresh.?token|private.?key|certificate.?password", re.I)
    forbidden = re.compile(r"(^|/)(?:\.venv|__pycache__|\.pytest_cache|build|dist|release|models|thumbnails|\.env|LocalAppData)(/|$)|\.(?:db|sqlite3?|safetensors|onnx|pt|pth|nef|nrw|pfx|p12|pem|key|dmp|log)$", re.I)
    report = dict(head=git("rev-parse", "HEAD").decode().strip(),
                  commits=git("rev-list", "--all").decode().splitlines(),
                  tracked_count=len(tracked), tracked_forbidden=sorted(p for p in tracked if forbidden.search(p)),
                  large_blobs=[], history_private_paths=[], history_credential_patterns=[], keyword_review=[])
    for row in inventory:
        kind, oid, size, *path = row.split(" ", 3)
        if kind != "blob":
            continue
        name = path[0] if path else ""
        if int(size) > 10 * 1024 * 1024:
            commits = git("log", "--all", "--format=%H", "--find-object=" + oid).decode().splitlines()
            report["large_blobs"].append(dict(path=name, bytes=int(size), oid=oid, commits=commits, current=name in tracked))
        data = git("cat-file", "blob", oid)
        if b"\0" in data:
            continue
        text = data.decode("utf8", errors="replace")
        for category, pattern in (("history_private_paths", private), ("history_credential_patterns", credentials), ("keyword_review", keyword)):
            lines = [i for i, line in enumerate(text.splitlines(), 1) if pattern.search(line)]
            if lines:
                report[category].append(dict(path=name, oid=oid, lines=lines,
                    commits=git("log", "--all", "--format=%H", "--find-object=" + oid).decode().splitlines()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({key: len(value) if isinstance(value, list) else value for key, value in report.items()
                      if key not in {"commits", "keyword_review"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
