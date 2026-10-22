#!/usr/bin/env python3
"""把 4_考学信息一览.csv 同步到飞书多维表格。

- 唯一键：编号。有就更新（只更新有变化的记录），没有就新增。
- CSV 里没有、飞书里有的记录：不删除，只在日志里列出。
- 飞书里编号为空的记录：跳过。
- 只用 Python 标准库，无需 pip install。

环境变量：
  FEISHU_APP_ID / FEISHU_APP_SECRET   必填（GitHub Secrets）
  FEISHU_APP_TOKEN / FEISHU_TABLE_ID  多维表格 app_token 和 table_id
  CSV_PATH                            默认 4_考学信息一览.csv
  DRY_RUN                             "true" 时只打印差异，不写入
"""
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://open.feishu.cn/open-apis"
JST = timezone(timedelta(hours=9))
KEY = "编号"

# CSV 列名 -> 飞书字段名。CSV 列名写成元组时，按顺序取第一个存在的列（兼容改名前后）。
MAPPING = [
    (("编号",), "编号"),
    (("大学",), "大学"),
    (("推荐等级",), "推荐等级"),
    (("所在地",), "所在地"),
    (("高才加分", "高度人才加点对象"), "高才加分"),
    (("研究科",), "研究科"),
    (("専攻",), "専攻"),
    (("包含学科", "领域"), "包含学科"),
    (("春夏_出願时间",), "春夏_出願时间"),
    (("春夏_試験时间",), "春夏_試験时间"),
    (("秋冬_出願时间",), "秋冬_出願时间"),
    (("秋冬_試験时间",), "秋冬_試験时间"),
    (("英語",), "英語"),
    (("日本語",), "日本語"),
    (("筆記内容",), "筆記内容"),
    (("口述|面接内容", "口述/面接内容"), "口述|面接内容"),
    # 過去問：不同步（飞书侧单独维护）
    (("事前連絡",), "事前連絡"),
    (("定員|倍率", "定員/倍率"), "定員|倍率"),
    (("募集要項",), "募集要項"),
    (("更新日期",), "更新日期"),
]
MULTI_SPLIT = {"包含学科": ";"}  # 多选字段的拆分符；未列出的多选字段整体作为一个选项

# 飞书字段类型
T_TEXT, T_NUMBER, T_SINGLE, T_MULTI, T_DATE, T_URL = 1, 2, 3, 4, 5, 15


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- HTTP
def call(method, path, token=None, body=None, query=None):
    url = API + path
    if query:
        url += "?" + "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in query.items())
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(6):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json; charset=utf-8")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(2 ** attempt)
                continue
            sys.exit(f"HTTP {e.code} {method} {path}: {raw[:500]}")
        except urllib.error.URLError as e:
            if attempt < 5:
                time.sleep(2 ** attempt)
                continue
            sys.exit(f"网络错误 {method} {path}: {e}")
        code = res.get("code", 0)
        if code == 0:
            return res.get("data") or res
        if code in (1254290, 1254291, 1254607, 99991400) and attempt < 5:  # 限流/写冲突
            time.sleep(2 ** attempt)
            continue
        sys.exit(f"飞书 API 错误 {code} {method} {path}: {res.get('msg')}")
    sys.exit(f"重试失败 {method} {path}")


def get_token(app_id, app_secret):
    res = call("POST", "/auth/v3/tenant_access_token/internal",
               body={"app_id": app_id, "app_secret": app_secret})
    return res["tenant_access_token"]


def list_fields(tok, app, tbl):
    out, page = {}, None
    while True:
        q = {"page_size": 100}
        if page:
            q["page_token"] = page
        d = call("GET", f"/bitable/v1/apps/{app}/tables/{tbl}/fields", tok, query=q)
        for f in d.get("items") or []:
            out[f["field_name"]] = f["type"]
        if not d.get("has_more"):
            return out
        page = d.get("page_token")


def list_records(tok, app, tbl, field_names):
    out, page = [], None
    while True:
        q = {"page_size": 500}
        if page:
            q["page_token"] = page
        d = call("POST", f"/bitable/v1/apps/{app}/tables/{tbl}/records/search", tok,
                 body={"field_names": field_names, "automatic_fields": False}, query=q)
        out.extend(d.get("items") or [])
        if not d.get("has_more"):
            return out
        page = d.get("page_token")


# ---------------------------------------------------------------- 值转换
def to_feishu(value, ftype, fname):
    """CSV 字符串 -> 飞书写入值。空值返回 None（清空）。"""
    v = (value or "").strip()
    if not v:
        return None
    if ftype == T_MULTI:
        sep = MULTI_SPLIT.get(fname)
        items = [s.strip() for s in v.split(sep)] if sep else [v]
        return [s for s in items if s] or None
    if ftype == T_DATE:
        dt = datetime.strptime(v[:10], "%Y-%m-%d").replace(tzinfo=JST)
        return int(dt.timestamp() * 1000)
    if ftype == T_URL:
        return {"link": v, "text": v}
    if ftype == T_NUMBER:
        return float(v)
    return v


def norm_existing(value, ftype):
    """飞书读出的值 -> 与 to_feishu 输出可比较的形式。"""
    if value in (None, "", []):
        return None
    if ftype == T_MULTI:
        return sorted(value) if isinstance(value, list) else [str(value)]
    if ftype == T_URL:
        if isinstance(value, dict):
            return value.get("link") or None
        return str(value)
    if ftype == T_DATE:
        return day(int(value))
    if ftype == T_NUMBER:
        return float(value)
    if isinstance(value, list):  # 文本字段读出来是 [{"type":"text","text":...}, ...]
        return "".join(seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in value) or None
    if isinstance(value, dict):
        return value.get("text") or value.get("link") or None
    return str(value)


def comparable(v, ftype):
    if v is None:
        return None
    if ftype == T_MULTI:
        return sorted(v)
    if ftype == T_URL:
        return v["link"]
    if ftype == T_DATE:
        return day(v)
    return v


def day(ms):
    """日期按日比较，避免时区导致的毫秒差异被当成变化。"""
    return datetime.fromtimestamp(ms / 1000, JST).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 主流程
def main():
    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    app = os.environ.get("FEISHU_APP_TOKEN")
    tbl = os.environ.get("FEISHU_TABLE_ID")
    csv_path = os.environ.get("CSV_PATH", "4_考学信息一览.csv")
    dry = os.environ.get("DRY_RUN", "").lower() == "true"
    if not all([app_id, app_secret, app, tbl]):
        sys.exit("缺少环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_APP_TOKEN / FEISHU_TABLE_ID")

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        rows = list(reader)
    log(f"CSV: {csv_path}，{len(rows)} 行")

    # 解析映射
    cols = []
    for csv_names, fname in MAPPING:
        col = next((c for c in csv_names if c in header), None)
        if col is None:
            sys.exit(f"CSV 里找不到列 {csv_names}")
        cols.append((col, fname))

    tok = get_token(app_id, app_secret)
    ftypes = list_fields(tok, app, tbl)
    missing = [fn for _, fn in cols if fn not in ftypes]
    if missing:
        sys.exit(f"飞书表里找不到字段：{missing}（请检查字段是否被改名）")

    # CSV -> 期望值
    key_col = next(c for c, fn in cols if fn == KEY)
    desired, dup_csv = {}, []
    for r in rows:
        key = (r.get(key_col) or "").strip()
        if not key:
            continue
        if key in desired:
            dup_csv.append(key)
        desired[key] = {fn: to_feishu(r.get(col), ftypes[fn], fn) for col, fn in cols}
    if dup_csv:
        log(f"⚠ CSV 中编号重复（以最后一行为准）：{dup_csv}")

    # 飞书现有记录
    names = [fn for _, fn in cols]
    recs = list_records(tok, app, tbl, names)
    log(f"飞书：{len(recs)} 条记录")
    existing, dup_fs, blank = {}, [], 0
    for rec in recs:
        fields = rec.get("fields") or {}
        key = norm_existing(fields.get(KEY), ftypes[KEY])
        if not key:
            blank += 1
            continue
        key = key.strip()
        if key in existing:
            dup_fs.append(key)
            continue
        existing[key] = (rec["record_id"],
                         {fn: norm_existing(fields.get(fn), ftypes[fn]) for fn in names})

    to_create, to_update, changes = [], [], []
    for key, want in desired.items():
        if key not in existing:
            to_create.append({"fields": {k: v for k, v in want.items() if v is not None}})
            continue
        rid, have = existing[key]
        diff = {}
        for fn, v in want.items():
            if comparable(v, ftypes[fn]) != have.get(fn):
                diff[fn] = v
        if diff:
            to_update.append({"record_id": rid, "fields": diff})
            changes.append((key, list(diff)))
    orphans = sorted(k for k in existing if k not in desired)

    log(f"新增 {len(to_create)}，更新 {len(to_update)}，未变化 {len(desired) - len(to_create) - len(to_update)}")
    for key, fl in changes[:200]:
        log(f"  更新 {key}: {', '.join(fl)}")
    if to_create:
        log("  新增：" + ", ".join(c["fields"][KEY] for c in to_create[:200]))
    if orphans:
        log(f"⚠ 飞书有、CSV 没有（未删除）{len(orphans)} 条：{', '.join(orphans[:100])}")
    if blank:
        log(f"⚠ 飞书中编号为空的记录 {blank} 条（已跳过）")
    if dup_fs:
        log(f"⚠ 飞书中编号重复（只更新第一条）：{dup_fs}")

    if dry:
        log("DRY_RUN=true，不写入。")
    else:
        base = f"/bitable/v1/apps/{app}/tables/{tbl}/records"
        for i in range(0, len(to_create), 500):
            call("POST", base + "/batch_create", tok, body={"records": to_create[i:i + 500]})
        for i in range(0, len(to_update), 500):
            call("POST", base + "/batch_update", tok, body={"records": to_update[i:i + 500]})
        log("写入完成。")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### 飞书同步{'（试运行）' if dry else ''}\n\n"
                    f"| 新增 | 更新 | 飞书有CSV无 | 编号为空 |\n|---|---|---|---|\n"
                    f"| {len(to_create)} | {len(to_update)} | {len(orphans)} | {blank} |\n\n")
            if changes:
                f.write("<details><summary>更新明细</summary>\n\n")
                for key, fl in changes[:500]:
                    f.write(f"- `{key}`: {', '.join(fl)}\n")
                f.write("\n</details>\n")


if __name__ == "__main__":
    main()
