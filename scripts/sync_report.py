"""Write the same change events to the Actions Summary and JSONL artifact."""
import html
import json
import os
from contextlib import contextmanager
from pathlib import Path


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
                with open(summary, "a", encoding="utf-8") as stream:
                    stream.write(f"### {self.name} 同步（{self.mode}） · {len(events)} 条\n\n")
                    stream.write("| 操作 | 文件 | 编号 | 専攻 | 旧链接 | 新链接 | 备注 |\n"
                                 "|---|---|---|---|---|---|---|\n")
                    for line in events[:300]:
                        values = list(json.loads(line).values())[1:]
                        cells = [html.escape(str(value)).replace("|", "&#124;")
                                 .replace("\n", " ").replace("\r", " ") for value in values]
                        stream.write("| " + " | ".join(cells) + " |\n")
                    if len(events) > 300:
                        stream.write("\n完整明细见 feishu-sync-report artifact。\n")
