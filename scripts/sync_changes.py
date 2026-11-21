"""Detect the full push range and prepare a safe fallback for missing history."""
import csv
import io
import os
import subprocess
from pathlib import Path

from sync_report import Report

CSV_PATH = "4_考学信息一览.csv"
ROOT = os.environ.get("MD_ROOT", "exam-monitor").rstrip("/")


def git(*args):
    return subprocess.check_output(["git", *args])


def resolve(ref):
    if not ref:
        return None
    result = subprocess.run(["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def csv_keys(rev):
    result = subprocess.run(["git", "show", f"{rev}:{CSV_PATH}"], capture_output=True)
    if result.returncode:
        return set()
    rows = csv.DictReader(io.StringIO(result.stdout.decode("utf-8-sig")))
    if "编号" not in (rows.fieldnames or []):
        raise ValueError(f"{rev} 的 CSV 缺少编号列")
    return {(row["编号"] or "").strip() for row in rows} - {""}


def main(report):
    head = resolve("HEAD")
    push = os.environ.get("EVENT") == "push"
    requested = os.environ.get("MD_COMMIT")
    target = resolve(requested) if requested and not push else head
    if not target:
        raise ValueError("找不到指定的 MD commit")
    base = resolve(os.environ.get("BEFORE")) if push else resolve(f"{target}^1")
    paths = set()
    deleted = set()
    if base:
        output = git("diff", "--name-only", "--no-renames", "-z", base, target, "--", ROOT)
        paths.update(os.fsdecode(path) for path in output.split(b"\0") if path.endswith(b".md"))
        # A historical manual commit selects MD only. Its CSV cannot authorize
        # deletions from the current Feishu table.
        if push and csv_keys(target):
            deleted = csv_keys(base) - csv_keys(target)
    for raw in os.environ.get("MD_PATHS", "").replace("，", ",").split(","):
        raw = raw.strip()
        if not raw:
            continue
        path = Path(raw)
        if path.is_absolute() or ".." in path.parts or path.suffix != ".md":
            raise ValueError(f"无效的 MD 路径：{raw}")
        paths.add(raw if raw.startswith(ROOT + "/") else f"{ROOT}/{raw}")
    Path("changed_md.txt").write_text("".join(path + "\n" for path in sorted(paths)), encoding="utf-8")
    Path("deleted_keys.txt").write_text("".join(key + "\n" for key in sorted(deleted)), encoding="utf-8")
    compare_all = not base
    # A workflow-only push validates deployment against Feishu without changes.
    preview = push and base and not git("diff", "--name-only", "-z", base, target, "--", ROOT, CSV_PATH)
    if preview:
        report.mode = "预览"
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as stream:
            stream.write(f"MD_COMPARE_ALL={str(compare_all).lower()}\n")
            if preview:
                stream.write("DRY_RUN=true\n")
    report.add("检测", file=CSV_PATH,
               detail=f"{base or '无可用基线'} → {target}，{len(paths)} 个 MD 路径，"
                      f"{len(deleted)} 个删除编号" + ("，比较全部 MD 内容" if compare_all else ""))


if __name__ == "__main__":
    with Report("changes", os.environ.get("DRY_RUN", "").lower() == "true").capture() as report:
        main(report)
