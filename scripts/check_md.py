#!/usr/bin/env python3
"""只读对照：仓库 exam-monitor/ 的 md 和飞书云盘文件夹，按路径列出哪边有、哪边没有。
不下载、不比较内容、不写入任何东西。顺带检查表格「编号」链接是否指向云盘里现存的 md。"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from sync_feishu import call, get_token, list_records, log  # noqa: E402
from sync_md import ID_RE, KEY, key_text, link_of, nfc  # noqa: E402


def list_folder(tok, folder):
    out, page = [], None
    while True:
        q = {"folder_token": folder, "page_size": 200}
        if page:
            q["page_token"] = page
        d = call("GET", "/drive/v1/files", tok, query=q)
        out.extend(d.get("files") or [])
        if not d.get("has_more"):
            return out
        page = d.get("next_page_token")


def walk(tok, root):
    files, dups, all_tokens = {}, [], set()
    level = [(root, "")]
    with ThreadPoolExecutor(4) as ex:
        while level:
            nxt = []
            for (_, pre), items in zip(level, ex.map(lambda x: list_folder(tok, x[0]), level)):
                for f in items:
                    rel = pre + nfc(f["name"])
                    if f["type"] == "folder":
                        nxt.append((f["token"], rel + "/"))
                    elif f["type"] == "file" and rel.endswith(".md"):
                        all_tokens.add(f["token"])
                        if rel in files:
                            dups.append(rel)
                        else:
                            files[rel] = f["token"]
            level = nxt
    return files, dups, all_tokens


def main():
    env = os.environ
    md_root = Path(env.get("MD_ROOT", "exam-monitor"))
    tok = get_token(env["FEISHU_APP_ID"], env["FEISHU_APP_SECRET"])

    local = {nfc(p.relative_to(md_root).as_posix()) for p in md_root.rglob("*.md")}
    remote, dups, tokens = walk(tok, env["FEISHU_FOLDER_TOKEN"])
    only_repo = sorted(local - set(remote))
    only_drive = sorted(set(remote) - local)
    log(f"仓库 md {len(local)}，云盘 md {len(remote)}，两边都有 {len(local & set(remote))}")
    log(f"只在仓库（云盘缺）{len(only_repo)}：")
    for x in only_repo:
        log(f"  + {x}")
    log(f"只在云盘（仓库无）{len(only_drive)}：")
    for x in only_drive:
        log(f"  - {x}")
    if dups:
        log(f"云盘同名重复 {len(dups)}：")
        for x in dups:
            log(f"  ! {x}")

    # 「编号」链接检查
    key2rel = {}
    for rel in local:
        m = ID_RE.match(rel.rpartition("/")[2])
        if m:
            key2rel[m.group(1)] = rel
    no_link, broken = [], []
    for rec in list_records(tok, env["FEISHU_APP_TOKEN"], env["FEISHU_TABLE_ID"], [KEY]):
        v = rec.get("fields", {}).get(KEY)
        key = key_text(v)
        if not key:
            continue
        t = link_of(v)
        if not t:
            no_link.append(key)
        elif t not in tokens:
            broken.append(key)
    log(f"「编号」无 md 链接 {len(no_link)}：{', '.join(no_link[:100])}")
    log(f"「编号」链接指向的文件不在云盘文件夹里 {len(broken)}：{', '.join(broken[:100])}")

    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### md 对照（只看有无，不比内容）\n\n"
                    "| 仓库 | 云盘 | 只在仓库 | 只在云盘 | 云盘重复 | 编号无链接 | 编号链接失效 |\n"
                    "|---|---|---|---|---|---|---|\n"
                    f"| {len(local)} | {len(remote)} | {len(only_repo)} | {len(only_drive)} | {len(dups)} "
                    f"| {len(no_link)} | {len(broken)} |\n\n")
            for title, items in (("只在仓库（云盘缺）", only_repo), ("只在云盘（仓库无）", only_drive),
                                 ("云盘同名重复", dups), ("编号无链接", no_link), ("编号链接失效", broken)):
                if items:
                    f.write(f"<details><summary>{title}（{len(items)}）</summary>\n\n" +
                            "".join(f"- `{x}`\n" for x in items) + "\n</details>\n\n")


if __name__ == "__main__":
    main()
