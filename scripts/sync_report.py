"""Write the same change events to the Actions Summary and JSONL artifact."""
import html
import json
import os
from contextlib import contextmanager
from pathlib import Path


COLUMNS = {"action": "操作", "file": "文件", "key": "编号", "major": "専攻",
           "old_link": "旧链接", "new_link": "新链接", "detail": "备注"}
TITLES = {"changes": "1. 本次改动", "table": "2. 表4", "md": "3. md 链接"}


def scope_keys():
    """本次要处理的编号（由各 job 设置的清单决定）；返回 None 表示全部（push 找不到基线时）
    表4：TABLE_KEYS_LIST + DELETED_KEYS_LIST；md：MD_CHANGED_LIST 里文件名的编号"""
    import re

    if os.environ.get("SCOPE_ALL", "true").lower() == "true":
        return None
    keys = set()
    for env in ("TABLE_KEYS_LIST", "DELETED_KEYS_LIST"):
        path = os.environ.get(env)
        if path and os.path.exists(path):
            keys |= {line.strip() for line in open(path, encoding="utf-8") if line.strip()}
    path = os.environ.get("MD_CHANGED_LIST")
    if path and os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            match = re.match(r"^(\d{3}[A-Z]-\d{2})", Path(line.strip()).name)
            if match:
                keys.add(match.group(1))
    return keys


class Report:
    def __init__(self, name, dry):
        self.name = name
        self.mode = "预览" if dry else "执行"
        folder = Path(os.environ.get("SYNC_REPORT_DIR", "sync-report"))
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / f"{name}.jsonl"
        self.path.write_text("", encoding="utf-8")

    def add(self, action, *, file="", key="", major="", old="", new="", detail=""):
        event = dict(mode=self.mode, action=action, file=file, key=key,
                     major=major, old_link=old, new_link=new, detail=detail)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    @contextmanager
    def capture(self):
        try:
            yield self
        except (Exception, SystemExit) as error:
            self.add("失败", detail=f"同步中止：{error}")
            raise
        finally:
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                events = self.path.read_text(encoding="utf-8").splitlines()
                title = TITLES.get(self.name, self.name)
                with open(summary, "a", encoding="utf-8") as stream:
                    if not events:
                        stream.write(f"### {title}（{self.mode}）：无修改\n\n")
                    else:
                        rows = [json.loads(line) for line in events[:300]]
                        # 只显示有内容的列（例如表4 不改链接，就不显示链接列）
                        cols = [c for c in COLUMNS if c == "action" or any(r.get(c) for r in rows)]
                        stream.write(f"### {title}（{self.mode}） · {len(events)} 条\n\n"
                                     "| " + " | ".join(COLUMNS[c] for c in cols) + " |\n"
                                     "|" + "---|" * len(cols) + "\n")
                        for row in rows:
                            cells = [html.escape(str(row.get(c, ""))).replace("|", "&#124;")
                                     .replace("\n", " ").replace("\r", " ") for c in cols]
                            stream.write("| " + " | ".join(cells) + " |\n")
                    if len(events) > 300:
                        stream.write("\n完整明细见 feishu-sync-report artifact。\n")
