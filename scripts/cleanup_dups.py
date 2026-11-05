#!/usr/bin/env python3
"""清理云盘里的 md，移到飞书回收站（可恢复）：
1. 同名重复：每组保留「専攻」链接指向的那一份，其余移到回收站；没有任何一份被链接的组不动，只列出。
2. 只在云盘、仓库 exam-monitor/ 里没有的 md（多半是仓库里改名/删除后留下的旧文件）：移到回收站。
   仍被表格「専攻」链接着的不删，只列出；一次超过 MAX_ORPHAN_DELETE（默认 50）个则全部不删并报错。
DRY_RUN=true 时只列出计划。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from check_md import list_folder  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
from sync_feishu import call, get_token, list_records, log  # noqa: E402
from sync_md import LINK, link_of, nfc  # noqa: E402


def walk_groups(tok, root):
    groups = {}
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
                        groups.setdefault(rel, []).append(f["token"])
            level = nxt
    return groups


def main():
    env = os.environ
    dry = env.get("DRY_RUN", "").lower() == "true"
    tok = get_token(env["FEISHU_APP_ID"], env["FEISHU_APP_SECRET"])
    groups = walk_groups(tok, env["FEISHU_FOLDER_TOKEN"])
    dups = {k: v for k, v in groups.items() if len(v) > 1}
    linked = {link_of(r.get("fields", {}).get(LINK))
              for r in list_records(tok, env["FEISHU_APP_TOKEN"], env["FEISHU_TABLE_ID"], [LINK])}
    linked.discard(None)
    log(f"同名重复：{len(dups)} 组")

    trashed, skipped, failed = [], [], []

    def trash(t, rel, bucket):
        if dry:
            log(f"  · 将移到回收站 {rel}（{t}）")
            bucket.append(rel)
            return
        try:
            call("DELETE", f"/drive/v1/files/{t}", tok, query={"type": "file"})
            bucket.append(rel)
            log(f"  ✓ 已移到回收站 {rel}（{t}）")
        except SystemExit as e:
            failed.append(rel)
            log(f"  ✗ 失败 {rel}：{e}")
    for rel, toks in sorted(dups.items()):
        keep = [t for t in toks if t in linked]
        if len(keep) != 1:
            skipped.append(f"{rel}（{len(toks)} 份，被链接 {len(keep)} 份）")
            log(f"  ? 跳过 {rel}：{len(toks)} 份中被「{LINK}」链接的有 {len(keep)} 份")
            continue
        for t in toks:
            if t != keep[0]:
                trash(t, rel, trashed)
        groups[rel] = keep  # 已处理的副本不再参与下面的「只在云盘」判断

    # 只在云盘的 md
    md_root = Path(env.get("MD_ROOT", "exam-monitor"))
    if not md_root.is_dir():
        sys.exit(f"找不到仓库目录 {md_root}，为防误删已中止")
    local = {nfc(p.relative_to(md_root).as_posix()) for p in md_root.rglob("*.md")}
    if not local:
        sys.exit("仓库里一个 md 都没有，为防误删已中止")
    orphans = sorted(rel for rel in groups if rel not in local)
    orphan_linked = [rel for rel in orphans if any(t in linked for t in groups[rel])]
    orphan_del = [rel for rel in orphans if rel not in orphan_linked]
    log(f"只在云盘：{len(orphans)} 个（其中仍被「{LINK}」链接、不删 {len(orphan_linked)} 个）")
    for rel in orphan_linked:
        log(f"  ? 跳过（仍被链接）{rel}")
    max_del = int(env.get("MAX_ORPHAN_DELETE", "50"))
    if len(orphan_del) > max_del:
        sys.exit(f"只在云盘的 md 要删 {len(orphan_del)} 个，超过上限 MAX_ORPHAN_DELETE={max_del}，为防误删已中止")
    orphan_trashed = []
    for rel in orphan_del:
        for t in groups[rel]:
            trash(t, rel, orphan_trashed)

    log(f"结果：重复移到回收站 {len(trashed)}，只在云盘移到回收站 {len(orphan_trashed)}，"
        f"跳过 {len(skipped) + len(orphan_linked)}，失败 {len(failed)}" + ("（试运行）" if dry else ""))
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### 清理云盘 md{'（试运行）' if dry else ''}\n\n"
                    "| 重复组 | 重复移到回收站 | 只在云盘 | 只在云盘移到回收站 | 跳过 | 失败 |\n|---|---|---|---|---|---|\n"
                    f"| {len(dups)} | {len(trashed)} | {len(orphans)} | {len(orphan_trashed)} "
                    f"| {len(skipped) + len(orphan_linked)} | {len(failed)} |\n\n")
            for title, items in (("重复移到回收站", trashed), ("只在云盘移到回收站", orphan_trashed),
                                 ("跳过（需人工判断）", skipped), ("只在云盘但仍被链接（未删）", orphan_linked),
                                 ("失败", failed)):
                if items:
                    f.write(f"<details><summary>{title}</summary>\n\n" +
                            "".join(f"- `{x}`\n" for x in items) + "\n</details>\n\n")
    if failed:
        sys.exit(f"{len(failed)} 个文件删除失败")


if __name__ == "__main__":
    main()
