#!/usr/bin/env python3
"""把 4_考学信息一览.csv 和 exam-monitor/ 下的 md 按「编号」对上，生成网页数据。

用法：python3 web/build.py
输出：
  web/data/rows.json        表4每一行（加上 md 是否存在），以及只有 md、不在表4里的専攻
  web/data/md/{大学编号}.json  该大学所有 md 正文 {编号: 正文}
"""
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "data"
TABLE4 = ROOT / "4_考学信息一览.csv"
TABLE3 = ROOT / "3_大学研究科専攻一覧.csv"
TABLE1 = ROOT / "1_大学ランク一覧.csv"
MD_DIR = ROOT / "exam-monitor"
ID_RE = re.compile(r"^(\d{3}[A-Z]+-\d+)")


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main():
    rows = read_csv(TABLE4)
    columns = list(rows[0].keys())

    md_files = {}
    for p in sorted(MD_DIR.rglob("*.md")):
        m = ID_RE.match(p.name)
        if m:
            md_files[m.group(1)] = p

    t3 = {r["専攻编号"]: r for r in read_csv(TABLE3)}
    t1 = {r["大学编号"]: r for r in read_csv(TABLE1)}

    out_rows = []
    seen = set()
    for r in rows:
        rid = r["编号"]
        seen.add(rid)
        p = md_files.get(rid)
        out_rows.append({**r, "_md": p.relative_to(ROOT).as_posix() if p else ""})

    # 只有 md、不在表4里的専攻：从表3/表1补上基本信息
    for rid, p in md_files.items():
        if rid in seen:
            continue
        s = t3.get(rid, {})
        u = t1.get(rid[:3], {})
        row = {c: "" for c in columns}
        row.update({
            "编号": rid,
            "大学": s.get("大学") or u.get("大学", ""),
            "推荐等级": u.get("推荐等级", ""),
            "所在地": u.get("本部所在地", ""),
            "高度人才加点对象": u.get("高度人才加点对象", ""),
            "研究科": s.get("研究科", ""),
            "専攻": (s.get("専攻", "") + s.get("细分", "")) or p.stem[len(rid):],
        })
        out_rows.append({**row, "_md": p.relative_to(ROOT).as_posix(), "_only_md": True})

    out_rows.sort(key=lambda r: r["编号"])

    (OUT / "md").mkdir(parents=True, exist_ok=True)
    for old in (OUT / "md").glob("*.json"):
        old.unlink()
    by_uni = {}
    for rid, p in md_files.items():
        by_uni.setdefault(rid[:3], {})[rid] = p.read_text(encoding="utf-8")
    for uni, docs in by_uni.items():
        (OUT / "md" / f"{uni}.json").write_text(
            json.dumps(docs, ensure_ascii=False), encoding="utf-8")

    (OUT / "rows.json").write_text(
        json.dumps({"columns": columns, "rows": out_rows}, ensure_ascii=False),
        encoding="utf-8")

    missing = [r["编号"] for r in out_rows if not r["_md"]]
    only_md = [r["编号"] for r in out_rows if r.get("_only_md")]
    print(f"表4 {len(rows)} 行，md {len(md_files)} 个，按编号对上 {len(rows) - len(missing)} 个")
    print(f"表4有但缺md {len(missing)} 个：{' '.join(missing)}")
    print(f"有md但不在表4 {len(only_md)} 个：{' '.join(only_md)}")


if __name__ == "__main__":
    main()
