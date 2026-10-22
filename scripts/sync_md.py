#!/usr/bin/env python3
"""把 exam-monitor/ 下的 md 同步到飞书云盘文件夹，并维护多维表格「编号」里的文件链接。

每次运行都把仓库里所有 md 和云盘里的文件逐个下载对比（不依赖历史状态）：
- 内容不同：上传新文件 -> 把表格「编号」链接指向新文件 -> 确认写入成功后把旧文件移到回收站
  （飞书 API 不能覆盖已上传文件，只能换新文件，所以 file token 会变）
- 云盘没有：按 大学/研究科/ 结构建文件夹并上传，再补上「编号」链接
- 仓库没有、云盘有：只记日志，不删除
- 「编号」链接缺失或指向的不是当前文件：修正链接
只用 Python 标准库。

环境变量：
  FEISHU_APP_ID / FEISHU_APP_SECRET   必填
  FEISHU_APP_TOKEN / FEISHU_TABLE_ID  多维表格
  FEISHU_FOLDER_TOKEN                 云盘根文件夹（对应仓库 exam-monitor/）
  FEISHU_DOMAIN                       如 https://ccn3ynd15sp4.feishu.cn
  MD_ROOT                             默认 exam-monitor
  DRY_RUN                             "true" 时只打印差异
  MD_LIMIT                            >0 时最多处理这么多个改动文件（试跑用）
"""
import json
import os
import re
import sys
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from sync_feishu import API, call, get_token, list_records, log  # noqa: E402

import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

KEY = "编号"
ID_RE = re.compile(r"^(\d{3}[A-Z]-\d{2})")


# ---------------------------------------------------------------- 云盘 API
def raw_request(method, path, token, data=None, headers=None):
    for attempt in range(6):
        req = urllib.request.Request(API + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(2 ** attempt)
                continue
            sys.exit(f"HTTP {e.code} {method} {path}: {e.read()[:300]!r}")
        except urllib.error.URLError as e:
            if attempt < 5:
                time.sleep(2 ** attempt)
                continue
            sys.exit(f"网络错误 {method} {path}: {e}")


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


def nfc(s):
    """统一成 NFC：Mac 上传的文件名常是 NFD（ジ = シ + ゛），看起来一样但字节不同"""
    return unicodedata.normalize("NFC", s)


def walk(tok, folder):
    """逐层并发列目录。返回 files{NFC relpath: token}, folders{NFC relpath: token}, dups"""
    files, folders, dups = {}, {"": folder}, []
    level = [(folder, "")]
    with ThreadPoolExecutor(4) as ex:
        while level:
            nxt = []
            for (ft, pre), items in zip(level, ex.map(lambda x: list_folder(tok, x[0]), level)):
                for f in items:
                    rel = pre + nfc(f["name"])
                    if f["type"] == "folder":
                        if rel in folders:
                            dups.append(rel + "/")
                        folders.setdefault(rel, f["token"])
                        nxt.append((f["token"], rel + "/"))
                    elif f["type"] == "file":
                        if rel in files:
                            dups.append(rel)
                        else:
                            files[rel] = f["token"]
            level = nxt
    return files, folders, dups


def download(tok, file_token):
    return raw_request("GET", f"/drive/v1/files/{file_token}/download", tok)


def upload(tok, folder_token, name, content):
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in (("file_name", name), ("parent_type", "explorer"),
                 ("parent_node", folder_token), ("size", str(len(content)))):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="file.md"\r\n'
                 f"Content-Type: application/octet-stream\r\n\r\n".encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    res = json.loads(raw_request("POST", "/drive/v1/files/upload_all", tok, b"".join(parts),
                                 {"Content-Type": f"multipart/form-data; boundary={boundary}"}))
    if res.get("code") != 0:
        sys.exit(f"上传失败 {name}: {res}")
    return res["data"]["file_token"]


def ensure_folder(tok, folders, rel_dir):
    """按需逐级创建文件夹，返回 folder token"""
    if rel_dir in folders:
        return folders[rel_dir]
    parent_rel, _, name = rel_dir.rpartition("/")
    parent = ensure_folder(tok, folders, parent_rel)
    d = call("POST", "/drive/v1/files/create_folder", tok, body={"name": name, "folder_token": parent})
    folders[rel_dir] = d["token"]
    log(f"  建文件夹 {rel_dir}")
    return d["token"]


def trash(tok, file_token):
    call("DELETE", f"/drive/v1/files/{file_token}", tok, query={"type": "file"})


# ---------------------------------------------------------------- 表格链接
def link_of(value):
    """从「编号」读出的值里取链接里的 file token"""
    if isinstance(value, list):
        for seg in value:
            if isinstance(seg, dict) and seg.get("link"):
                m = re.search(r"/file/([A-Za-z0-9]+)", seg["link"])
                if m:
                    return m.group(1)
    return None


def set_link(tok, app, tbl, record_id, key, file_token, domain):
    url = f"{domain}/file/{file_token}"
    base = f"/bitable/v1/apps/{app}/tables/{tbl}/records/{record_id}"
    call("PUT", base, tok, body={"fields": {KEY: [{"type": "url", "text": key, "link": url}]}})
    got = call("GET", base, tok)
    if link_of(got.get("record", {}).get("fields", {}).get(KEY)) != file_token:
        return False
    return True


# ---------------------------------------------------------------- 主流程
def main():
    env = os.environ
    app_id, app_secret = env.get("FEISHU_APP_ID"), env.get("FEISHU_APP_SECRET")
    app, tbl = env.get("FEISHU_APP_TOKEN"), env.get("FEISHU_TABLE_ID")
    root_folder, domain = env.get("FEISHU_FOLDER_TOKEN"), (env.get("FEISHU_DOMAIN") or "").rstrip("/")
    md_root = Path(env.get("MD_ROOT", "exam-monitor"))
    dry = env.get("DRY_RUN", "").lower() == "true"
    limit = int(env.get("MD_LIMIT") or 0)
    if not all([app_id, app_secret, app, tbl, root_folder, domain]):
        sys.exit("缺少环境变量")

    tok = get_token(app_id, app_secret)

    local = {nfc(p.relative_to(md_root).as_posix()): p for p in sorted(md_root.rglob("*.md"))}
    log(f"仓库 md：{len(local)} 个")
    remote, folders, dups = walk(tok, root_folder)
    remote_md = {k: v for k, v in remote.items() if k.endswith(".md")}
    log(f"云盘 md：{len(remote_md)} 个，文件夹 {len(folders) - 1} 个")
    if dups:
        log(f"⚠ 云盘里同路径重复文件（只比较第一个）：{dups[:50]}")

    # 下载对比
    common = [k for k in local if k in remote_md]
    def same(rel):
        return rel, download(tok, remote_md[rel]) == local[rel].read_bytes()
    with ThreadPoolExecutor(4) as ex:
        results = dict(ex.map(same, common))
    changed = [k for k in common if not results[k]]
    new = [k for k in local if k not in remote_md]
    orphans = sorted(k for k in remote_md if k not in local)

    # 表格记录（只取编号）
    recs = list_records(tok, app, tbl, [KEY])
    by_key = {}
    for rec in recs:
        v = rec.get("fields", {}).get(KEY)
        text = "".join(s.get("text", "") for s in v if isinstance(s, dict)) if isinstance(v, list) else (v or "")
        if text.strip():
            by_key.setdefault(text.strip(), (rec["record_id"], link_of(v)))

    log(f"内容有变化 {len(changed)}，云盘缺少 {len(new)}，云盘多出 {len(orphans)}（不删除）")
    for k in changed[:200]:
        log(f"  变化 {k}")
    for k in new[:200]:
        log(f"  新增 {k}")
    for k in orphans[:100]:
        log(f"  ⚠ 仓库已没有 {k}")

    todo = changed + new
    if limit > 0:
        todo = todo[:limit]
        log(f"MD_LIMIT={limit}，本次只处理：{todo}")

    replaced, uploaded, relinked, link_fail, no_record = [], [], [], [], []
    current = dict(remote_md)  # rel -> 当前应链接的 file token

    if not dry:
        for rel in todo:
            folder = ensure_folder(tok, folders, rel.rpartition("/")[0])
            new_tok = upload(tok, folder, rel.rpartition("/")[2], local[rel].read_bytes())
            old_tok = remote_md.get(rel)
            m = ID_RE.match(rel.rpartition("/")[2])
            key = m.group(1) if m else None
            if key and key in by_key:
                rid, _ = by_key[key]
                if not set_link(tok, app, tbl, rid, key, new_tok, domain):
                    trash(tok, new_tok)  # 回滚：保留旧文件和旧链接
                    link_fail.append(rel)
                    log(f"  ✗ 链接写入失败，已撤回新文件：{rel}")
                    continue
                by_key[key] = (rid, new_tok)
            elif key:
                no_record.append(rel)
            current[rel] = new_tok
            if old_tok:
                trash(tok, old_tok)
                replaced.append(rel)
            else:
                uploaded.append(rel)
            log(f"  ✓ {'替换' if old_tok else '上传'} {rel}")

    # 链接校对：表格「编号」应指向当前文件
    fix = []
    for rel, ftok in current.items():
        if rel not in local:
            continue
        m = ID_RE.match(rel.rpartition("/")[2])
        if not m or m.group(1) not in by_key:
            continue
        rid, linked = by_key[m.group(1)]
        if linked != ftok:
            fix.append((rel, m.group(1), rid, ftok))
    log(f"「编号」链接需要修正：{len(fix)}")
    for rel, key, _, _ in fix[:100]:
        log(f"  链接 {key} -> {rel}")
    if not dry:
        for rel, key, rid, ftok in fix:
            (relinked if set_link(tok, app, tbl, rid, key, ftok, domain) else link_fail).append(rel)

    if dry:
        log("DRY_RUN=true，不写入。")

    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### md 同步{'（试运行）' if dry else ''}\n\n"
                    f"| 内容变化 | 云盘缺少 | 云盘多出 | 链接待修正 |\n|---|---|---|---|\n"
                    f"| {len(changed)} | {len(new)} | {len(orphans)} | {len(fix)} |\n\n")
            if not dry:
                f.write(f"替换 {len(replaced)}，新上传 {len(uploaded)}，修正链接 {len(relinked)}，"
                        f"失败 {len(link_fail)}，表格无对应编号 {len(no_record)}\n\n")
            for title, items in (("内容变化", changed), ("云盘缺少", new), ("云盘多出（未删除）", orphans),
                                 ("链接失败", link_fail)):
                if items:
                    f.write(f"<details><summary>{title}</summary>\n\n" +
                            "".join(f"- `{x}`\n" for x in items[:500]) + "\n</details>\n\n")
    if link_fail:
        sys.exit(f"{len(link_fail)} 个文件的链接写入失败")


if __name__ == "__main__":
    main()
