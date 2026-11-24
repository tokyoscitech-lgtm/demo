#!/usr/bin/env python3
"""只读：按文件名里的日期，核对表4「専攻」链接的 md 是不是最新的。不下载、不写入任何东西。

对每条记录比较三个日期：
  L  链接文件名里的日期（001C-04機械工学専攻_2026-11-24.md → 2026-11-24）
  F  飞书表格的「更新日期」
  C  仓库 4_考学信息一览.csv 的「更新日期」
另外 R 是仓库里该编号的 md 文件名日期。

规则（谁的日期新跟谁走，仓库是唯一来源）：
  L == F            一致，不报告
  L <  F            链接的 md 比表格旧：仓库有更新的 md 时，sync_md 会换链接；没有就要补 md
  L >  F            md 比表格新：表4（CSV）日期比飞书新时，sync_feishu 会更新飞书；否则不动，提示去改表4
  没有日期           文件名没日期 / 表格日期为空：只列出来
"""
import csv
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from check_md import walk  # noqa: E402
from sync_feishu import day, get_token, list_records, log  # noqa: E402
from sync_md import (CSV_PATH, ID_RE, KEY, LINK, key_text, link_of, link_url,  # noqa: E402
                     md_date, tracked_paths)
from sync_report import Report  # noqa: E402

DATE_FIELD = os.environ.get("DATE_FIELD", "更新日期")
ISO = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")


def as_date(value):
    """飞书日期字段（毫秒时间戳）或文本 / CSV 字符串 → YYYY-MM-DD；读不出来返回 None"""
    if value in (None, "", []):
        return None
    if isinstance(value, (int, float)):
        return day(int(value))
    match = ISO.search(key_text(value) if not isinstance(value, str) else value)
    if not match:
        return None
    y, m, d = (int(x) for x in match.groups())
    return f"{y:04d}-{m:02d}-{d:02d}"


def main(report):
    env = os.environ
    md_root = env.get("MD_ROOT", "exam-monitor").rstrip("/")
    tok = get_token(env["FEISHU_APP_ID"], env["FEISHU_APP_SECRET"])

    with open(CSV_PATH, encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    csv_date = {(r.get(KEY) or "").strip(): as_date(r.get(DATE_FIELD)) for r in rows}
    majors = {(r.get(KEY) or "").strip(): (r.get("専攻") or "").strip() for r in rows}

    repo_date = defaultdict(lambda: None)
    for path in tracked_paths(md_root):
        name = Path(path).name
        match = ID_RE.match(name)
        if match and md_date(name):
            repo_date[match.group(1)] = max(filter(None, [repo_date[match.group(1)], md_date(name)]))

    _, _, names = walk(tok, env["FEISHU_FOLDER_TOKEN"])   # token -> 云盘相对路径

    stats = Counter()
    for rec in list_records(tok, env["FEISHU_APP_TOKEN"], env["FEISHU_TABLE_ID"], [KEY, LINK, DATE_FIELD]):
        fields = rec.get("fields", {})
        key = key_text(fields.get(KEY))
        if not key:
            continue
        url, token = link_url(fields.get(LINK)), link_of(fields.get(LINK))
        emit = lambda action, detail, file="": report.add(  # noqa: E731
            action, file=file, key=key, major=majors.get(key, ""), old=url, detail=detail)
        if not token:
            stats["无链接"] += 1
            emit("无链接", f"「{LINK}」没有 md 链接")
            continue
        if token not in names:
            stats["链接失效"] += 1
            emit("链接失效", "链接指向的文件不在云盘文件夹里")
            continue

        linked = names[token]
        L, F, C, R = md_date(linked), as_date(fields.get(DATE_FIELD)), csv_date.get(key), repo_date[key]
        if not L or not F:
            stats["无日期"] += 1
            emit("无日期", "链接文件名没有日期" if not L else f"表格「{DATE_FIELD}」为空", linked)
            continue
        if L < F:
            stats["md 较旧"] += 1
            if R and R > L:
                detail = f"链接的 md {L} 早于表格 {F}；仓库已有 {R} 的 md，下次同步会换链接"
            else:
                detail = f"链接的 md {L} 早于表格 {F}；仓库也没有更新的 md，需要补"
            emit("md 较旧", detail, linked)
        elif L > F:
            stats["md 较新"] += 1
            if C and C > F:
                detail = f"链接的 md {L} 晚于表格 {F}；表4 已是 {C}，下次同步写入飞书"
            else:
                detail = f"链接的 md {L} 晚于表格 {F}；表4 是 {C or '空'}，未改动，请更新表4"
            emit("md 较新", detail, linked)
        elif R and R > L:
            stats["待同步"] += 1
            emit("待同步", f"日期与表格一致（{L}），但仓库已有 {R} 的 md，下次同步会换链接", linked)
        else:
            stats["一致"] += 1

    log("「専攻」链接 md 日期核对：" + "，".join(f"{k} {v}" for k, v in stats.most_common()))


if __name__ == "__main__":
    with Report("date", True).capture() as rep:
        main(rep)
