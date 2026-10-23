#!/usr/bin/env python3
"""一次性探测：「编号」字段里的文件链接是什么结构、API 能用什么格式写入。
只写入「编号为空」的那条空记录（崇城大学），每次写完读回，最后恢复为空。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from sync_feishu import call, get_token, list_records  # noqa: E402

app, tbl = os.environ["FEISHU_APP_TOKEN"], os.environ["FEISHU_TABLE_ID"]
domain = os.environ["FEISHU_DOMAIN"].rstrip("/")
tok = get_token(os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"])
base = f"/bitable/v1/apps/{app}/tables/{tbl}/records"

recs = list_records(tok, app, tbl, ["编号", "大学"])
sample, blank = None, None
for r in recs:
    v = r.get("fields", {}).get("编号")
    txt = "".join(s.get("text", "") for s in v if isinstance(s, dict)) if isinstance(v, list) else (v or "")
    if txt.strip() == "001E-01":
        sample = r
    if not txt.strip():
        blank = r
print("== 001E-01 的「编号」原始值（search 接口）==")
print(json.dumps(sample["fields"]["编号"], ensure_ascii=False, indent=1))
got = call("GET", f"{base}/{sample['record_id']}", tok)
print("== 同一条（GET 接口）==")
print(json.dumps(got["record"]["fields"].get("编号"), ensure_ascii=False, indent=1))
print("== 空记录 ==", blank["record_id"], json.dumps(blank["fields"], ensure_ascii=False))

seg = sample["fields"]["编号"][0]
ftok = None
for s in sample["fields"]["编号"]:
    for k in ("token", "link"):
        if s.get(k):
            ftok = s[k].rsplit("/", 1)[-1]
            break
url = f"{domain}/file/{ftok}"
candidates = {
    "原样回写": sample["fields"]["编号"],
    "mention段": [{k: v for k, v in seg.items()}],
    "url段": [{"type": "url", "text": "PROBE", "link": url}],
    "markdown": f"[PROBE]({url})",
}


def try_write(name, value):
    import urllib.error  # noqa
    try:
        call("PUT", f"{base}/{blank['record_id']}", tok, body={"fields": {"编号": value}})
    except SystemExit as e:
        print(f"-- {name}: 写入失败 {e}")
        return
    back = call("GET", f"{base}/{blank['record_id']}", tok)["record"]["fields"].get("编号")
    print(f"-- {name}: 写入成功，读回 = {json.dumps(back, ensure_ascii=False)}")


for name, value in candidates.items():
    try_write(name, value)

call("PUT", f"{base}/{blank['record_id']}", tok, body={"fields": {"编号": None}})
back = call("GET", f"{base}/{blank['record_id']}", tok)["record"]["fields"].get("编号")
print("== 已恢复空记录，编号 =", back)
