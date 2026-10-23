#!/usr/bin/env python3
"""清理云盘里同名重复的 md：每组保留「编号」链接指向的那一份，其余移到飞书回收站（可恢复）。
没有任何一份被「编号」链接的组：不动，只列出。DRY_RUN=true 时只列出计划。"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from check_md import list_folder  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
from sync_feishu import call, get_token, list_records, log  # noqa: E402
from sync_md import KEY, link_of, nfc  # noqa: E402


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
    return {k: v for k, v in groups.items() if len(v) > 1}


def main():
    env = os.environ
    dry = env.get("DRY_RUN", "").lower() == "true"
    tok = get_token(env["FEISHU_APP_ID"], env["FEISHU_APP_SECRET"])
    dups = walk_groups(tok, env["FEISHU_FOLDER_TOKEN"])
    linked = {link_of(r.get("fields", {}).get(KEY))
              for r in list_records(tok, env["FEISHU_APP_TOKEN"], env["FEISHU_TABLE_ID"], [KEY])}
    linked.discard(None)
    log(f"同名重复：{len(dups)} 组")

    trashed, skipped, failed = [], [], []
    for rel, toks in sorted(dups.items()):
        keep = [t for t in toks if t in linked]
        if len(keep) != 1:
            skipped.append(f"{rel}（{len(toks)} 份，被链接 {len(keep)} 份）")
            log(f"  ? 跳过 {rel}：{len(toks)} 份中被「编号」链接的有 {len(keep)} 份")
            continue
        for t in toks:
            if t == keep[0]:
                continue
            if dry:
                log(f"  · 将移到回收站 {rel}（{t}）")
                trashed.append(rel)
                continue
            try:
                call("DELETE", f"/drive/v1/files/{t}", tok, query={"type": "file"})
                trashed.append(rel)
                log(f"  ✓ 已移到回收站 {rel}（{t}）")
            except SystemExit as e:
                failed.append(rel)
                log(f"  ✗ 失败 {rel}：{e}")

    log(f"结果：移到回收站 {len(trashed)}，跳过 {len(skipped)}，失败 {len(failed)}" + ("（试运行）" if dry else ""))
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### 清理重复 md{'（试运行）' if dry else ''}\n\n| 重复组 | 移到回收站 | 跳过 | 失败 |\n"
                    f"|---|---|---|---|\n| {len(dups)} | {len(trashed)} | {len(skipped)} | {len(failed)} |\n\n")
            for title, items in (("移到回收站", trashed), ("跳过（需人工判断）", skipped), ("失败", failed)):
                if items:
                    f.write(f"<details><summary>{title}</summary>\n\n" +
                            "".join(f"- `{x}`\n" for x in items) + "\n</details>\n\n")
    if failed:
        sys.exit(f"{len(failed)} 个文件删除失败")


if __name__ == "__main__":
    main()
