"""决定这次 Sync 要处理哪些编号：push 时取本次改动，手动运行时取指定编号。"""
import csv
import io
import os
import re
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


def csv_rows(rev):
    """编号 -> 整行内容（用来找出这次改动过的行）"""
    result = subprocess.run(["git", "show", f"{rev}:{CSV_PATH}"], capture_output=True)
    if result.returncode:
        return {}
    rows = csv.DictReader(io.StringIO(result.stdout.decode("utf-8-sig")))
    if "编号" not in (rows.fieldnames or []):
        raise ValueError(f"{rev} 的 CSV 缺少编号列")
    out = {}
    for row in rows:
        key = (row["编号"] or "").strip()
        if key:
            out[key] = tuple(sorted((k, (v or "").strip()) for k, v in row.items() if k))
    return out



KEY_RE = re.compile(r"^\d{3}[A-Z]-\d{2}$")
ID_RE = re.compile(r"^(\d{3}[A-Z]-\d{2})")


def parse_keys(text):
    """手动输入的编号：逗号（全角/半角）、空格、换行分隔"""
    return [k for k in re.split(r"[,，、\s]+", text or "") if k]


def write_list(name, items):
    Path(name).write_text("".join(item + "\n" for item in sorted(items)), encoding="utf-8")


def main(report):
    """两种模式：
    push（PR 合并）：只同步这次改动——CSV 新增/修改的行、删除的行、改动的 md
    手动运行：只同步指定编号——table_keys 同步表4 数据（不含 md），md_keys 同步 md
    """
    head = resolve("HEAD")
    push = os.environ.get("EVENT") == "push"
    table_keys, md_paths, deleted = set(), set(), set()
    base = None

    if push:
        base = resolve(os.environ.get("BEFORE"))
        if base:
            before, after = csv_rows(base), csv_rows(head)
            table_keys = {key for key, row in after.items() if before.get(key) != row}
            if after:
                deleted = set(before) - set(after)
            output = git("diff", "--name-only", "--no-renames", "-z", base, head, "--", ROOT)
            md_paths = {os.fsdecode(p) for p in output.split(b"\0") if p.endswith(b".md")}
        scope = f"{(base or '无可用基线')[:7]} → {head[:7]}"
    else:
        csv_now = csv_rows(head)
        tracked = [os.fsdecode(p) for p in git("ls-files", "-z", "--", ROOT).split(b"\0") if p.endswith(b".md")]
        for kind, raw in (("表4", os.environ.get("TABLE_KEYS")), ("md", os.environ.get("MD_KEYS"))):
            for key in parse_keys(raw):
                if not KEY_RE.match(key):
                    report.add("失败", key=key, detail=f"{kind}：编号格式不对（应为 001A-01 这样）")
                elif key not in csv_now:
                    report.add("失败", key=key, detail=f"{kind}：{CSV_PATH} 里没有这个编号")
                elif kind == "表4":
                    table_keys.add(key)
                else:
                    found = [p for p in tracked if (ID_RE.match(Path(p).name) or [None, None])[1] == key]
                    if found:
                        md_paths.update(found)
                    else:
                        report.add("失败", key=key, detail="md：仓库里没有这个编号的 md")
        scope = "手动指定编号"

    write_list("changed_md.txt", md_paths)
    write_list("table_keys.txt", table_keys)
    write_list("deleted_keys.txt", deleted)
    # 只有 push 却找不到基线（如首次提交）时才全表处理、比较全部 md 内容
    scope_all = push and not base
    # 只改了 workflow 的 push：自动预览，不写飞书
    preview = push and base and not git("diff", "--name-only", "-z", base, head, "--", ROOT, CSV_PATH)
    if preview:
        report.mode = "预览"
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as stream:
            stream.write(f"SCOPE_ALL={str(scope_all).lower()}\n")
            if preview:
                stream.write("DRY_RUN=true\n")
    md_keys = {m.group(1) for m in (ID_RE.match(Path(p).name) for p in md_paths) if m}
    report.add("检测", file=CSV_PATH,
               detail=f"{scope}：表4 {len(table_keys)} 个编号，md {len(md_keys)} 个编号，删除 {len(deleted)} 个编号"
                      + ("；没有基线，处理全部记录" if scope_all else ""))


if __name__ == "__main__":
    with Report("changes", os.environ.get("DRY_RUN", "").lower() == "true").capture() as report:
        main(report)
