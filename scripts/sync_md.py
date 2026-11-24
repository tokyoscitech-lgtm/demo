#!/usr/bin/env python3
"""Reconcile Markdown links by CSV key and verify replacements before trashing files."""
import json
import os
import re
import sys
import subprocess
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from sync_feishu import API, call, get_token, list_records, log  # noqa: E402

from sync_report import Report

KEY = "编号"                                       # 匹配键（纯文本，只读）
LINK = os.environ.get("LINK_FIELD", "専攻")       # md 链接所在的超链接字段
CSV_PATH = os.environ.get("CSV_PATH", "4_考学信息一览.csv")  # 链接字段没有文字时，取 CSV 的「専攻」作显示文字


def csv_texts():
    import csv

    with open(CSV_PATH, encoding="utf-8-sig", newline="") as stream:
        return {(row[KEY] or "").strip(): (row["専攻"] or "").strip()
                for row in csv.DictReader(stream)}


ID_RE = re.compile(r"^(\d{3}[A-Z]-\d{2})")
DATE_RE = re.compile(r"_(\d{4}-\d{2}-\d{2})\.md$")   # 001C-04機械工学専攻_2026-11-24.md


def md_date(name):
    """文件名末尾的日期（YYYY-MM-DD）；没有或不是合法日期时返回 None"""
    from datetime import date

    match = DATE_RE.search(nfc(name or ""))
    if not match:
        return None
    try:
        date.fromisoformat(match.group(1))
    except ValueError:
        return None
    return match.group(1)


def nfc(s):
    """统一成 NFC：Mac 上传的文件名常是 NFD（ジ = シ + ゛），看起来一样但字节不同"""
    return unicodedata.normalize("NFC", s)


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


class Drive:
    """按路径逐级查找文件夹/文件，列目录结果缓存，只访问用得到的目录"""

    def __init__(self, tok, root):
        self.tok, self.root, self.cache = tok, root, {}

    def ls(self, folder):
        if folder not in self.cache:
            out, page = [], None
            while True:
                q = {"folder_token": folder, "page_size": 200}
                if page:
                    q["page_token"] = page
                d = call("GET", "/drive/v1/files", self.tok, query=q)
                out.extend(d.get("files") or [])
                if not d.get("has_more"):
                    break
                page = d.get("next_page_token")
            self.cache[folder] = out
        return self.cache[folder]

    def folder(self, rel_dir, create=False):
        cur = self.root
        for part in [p for p in rel_dir.split("/") if p]:
            hit = next((f for f in self.ls(cur) if f["type"] == "folder" and nfc(f["name"]) == part), None)
            if not hit:
                if not create:
                    return None
                d = call("POST", "/drive/v1/files/create_folder", self.tok,
                         body={"name": part, "folder_token": cur})
                hit = {"name": part, "token": d["token"], "type": "folder"}
                self.cache[cur].append(hit)
                self.cache[d["token"]] = []
                log(f"  建文件夹 {part}")
            cur = hit["token"]
        return cur

    def files(self, folder, name):
        return [f["token"] for f in self.ls(folder) if f["type"] == "file" and nfc(f["name"]) == name]

    def download(self, file_token):
        return raw_request("GET", f"/drive/v1/files/{file_token}/download", self.tok)

    def upload(self, folder, name, content):
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in (("file_name", name), ("parent_type", "explorer"),
                     ("parent_node", folder), ("size", str(len(content)))):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="file.md"\r\n'
                     f"Content-Type: application/octet-stream\r\n\r\n".encode() + content + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        res = json.loads(raw_request("POST", "/drive/v1/files/upload_all", self.tok, b"".join(parts),
                                     {"Content-Type": f"multipart/form-data; boundary={boundary}"}))
        if res.get("code") != 0:
            sys.exit(f"上传失败 {name}: {res}")
        return res["data"]["file_token"]

    def trash(self, file_token):
        call("DELETE", f"/drive/v1/files/{file_token}", self.tok, query={"type": "file"})


# ---------------------------------------------------------------- 表格链接
def link_url(value):
    segments = value if isinstance(value, list) else [value]
    return next((seg["link"] for seg in segments
                 if isinstance(seg, dict) and seg.get("link")), "")


def link_of(value):
    match = re.search(r"/file/([A-Za-z0-9]+)(?:[?#]|$)", link_url(value))
    return match.group(1) if match else None


def key_text(value):
    if isinstance(value, dict):
        return (value.get("text") or "").strip()
    if isinstance(value, list):
        return "".join(s.get("text", "") for s in value if isinstance(s, dict)).strip()
    return (value or "").strip()


def set_link(tok, app, tbl, record_id, text, file_token, domain):
    """只写 LINK 字段（超链接），不碰「编号」。写入后读回确认；任何 API 错误都返回 False（由调用方保留两份文件）"""
    base = f"/bitable/v1/apps/{app}/tables/{tbl}/records/{record_id}"
    try:
        call("PUT", base, tok, body={"fields": {LINK: {"text": text, "link": f"{domain}/file/{file_token}"}}})
        got = call("GET", base, tok)
    except SystemExit as e:
        log(f"  ✗ 写入「{LINK}」链接出错：{e}")
        return False
    return link_of(got.get("record", {}).get("fields", {}).get(LINK)) == file_token


def tracked_paths(root):
    output = subprocess.check_output(["git", "ls-files", "-z", "--", root])
    return [os.fsdecode(path) for path in output.split(b"\0") if path.endswith(b".md")]


def select_path(paths, changed):
    # Git can contain NFC/NFD aliases. Treat identical blobs at the same
    # normalized path as one file. Conflicting versions need an explicit fix.
    canonical = {nfc(path) for path in paths}
    edited = set(paths) & changed
    if len(canonical) == 1 and len(edited) == 1:
        return edited.pop()
    if len(canonical) == 1:
        if len(paths) == 1 or len({Path(path).read_bytes() for path in paths}) == 1:
            return min(paths, key=lambda path: (path != nfc(path), path))
    raise ValueError("同一编号对应多个不同 MD：" + "、".join(paths))


def sync_file(path, record, drive, app, tbl, domain, md_root, compare, dry, emit, references, major, removed):
    rel_dir, _, name = nfc(path[len(md_root) + 1:]).rpartition("/")
    folder = drive.folder(rel_dir)
    candidates = drive.files(folder, name) if folder else []
    record_id = record["record_id"] if record else None
    value = record.get("fields", {}).get(LINK) if record else None
    old_url = link_url(value)
    old_token = link_of(value)
    if old_token in candidates and old_url == f"{domain}/file/{old_token}" and not compare:
        return

    # 文件名带日期时：链接指向的云盘文件比仓库这份新，就不拿旧的去替换（谁新跟谁走）
    if old_token and old_token not in candidates and folder:
        linked = next((nfc(f["name"]) for f in drive.ls(folder) if f.get("token") == old_token), None)
        if md_date(linked) and md_date(name) and md_date(linked) > md_date(name):
            emit("保留", old=old_url, detail=f"云盘链接的 {linked} 比仓库的 {name} 新，未替换")
            return

    local = Path(path).read_bytes()
    ordered = sorted(candidates, key=lambda token: (token != old_token, token))
    new = next((token for token in ordered if drive.download(token) == local), None)
    if new and old_url == f"{domain}/file/{new}":
        return

    action = "修复" if new else ("修改" if old_token or candidates else "新增")
    new_url = f"{domain}/file/{new}" if new else ""
    # Only retire the previously linked file when its identity and ownership
    # are clear. Other copies and files from unknown locations stay in place.
    retire = False
    retired_folder = None
    if old_token and old_token != new and references[old_token] == 1:
        if old_token in candidates:
            retired_folder = folder
        else:
            for old_path in removed:
                old_dir, _, old_name = nfc(old_path[len(md_root) + 1:]).rpartition("/")
                parent = drive.folder(old_dir)
                if parent and old_token in drive.files(parent, old_name):
                    retired_folder = parent
                    break
        retire = retired_folder is not None
    if dry:
        emit(action, old=old_url, new=new_url, detail="" if new else "需上传，成功后生成链接")
        if retire:
            emit("删除", old=old_url, new=new_url, detail="仅在新链接读回确认后移入回收站")
        return

    if not new:
        folder = folder or drive.folder(rel_dir, create=True)
        new = drive.upload(folder, name, local)
    new_url = f"{domain}/file/{new}"
    if not set_link(drive.tok, app, tbl, record_id, major, new, domain):
        emit("失败", old=old_url, new=new_url,
             detail="写入或读回链接失败，保留旧文件与新文件")
        return False

    emit(action, old=old_url, new=new_url)
    if old_token:
        references[old_token] -= 1
    references[new] += 1
    if retire:
        try:
            drive.trash(old_token)
        except SystemExit as error:
            emit("失败", old=old_url, new=new_url, detail=f"旧文件移入回收站失败：{error}")
            return False
        drive.cache[retired_folder] = [entry for entry in drive.cache[retired_folder]
                                      if entry["token"] != old_token]
        emit("删除", old=old_url, new=new_url, detail="新链接已读回确认")


def main(report, dry):
    from collections import Counter, defaultdict
    from functools import partial

    env = os.environ
    app_id, app_secret = env.get("FEISHU_APP_ID"), env.get("FEISHU_APP_SECRET")
    app, tbl = env.get("FEISHU_APP_TOKEN"), env.get("FEISHU_TABLE_ID")
    root_folder, domain = env.get("FEISHU_FOLDER_TOKEN"), (env.get("FEISHU_DOMAIN") or "").rstrip("/")
    md_root = env.get("MD_ROOT", "exam-monitor").rstrip("/")
    if not all([app_id, app_secret, app, tbl, root_folder, domain]):
        sys.exit("缺少飞书同步所需的环境变量")

    list_path = env.get("MD_CHANGED_LIST")
    changed = set(Path(list_path).read_text(encoding="utf-8").splitlines()) if list_path else set()
    compare_all = env.get("MD_COMPARE_ALL") == "true"
    texts = csv_texts()
    tok = get_token(app_id, app_secret)
    drive = Drive(tok, root_folder)
    rows, paths = defaultdict(list), defaultdict(list)
    references = Counter()
    for record in list_records(tok, app, tbl, [KEY, LINK]):
        fields = record.get("fields", {})
        rows[key_text(fields.get(KEY))].append(record)
        token = link_of(fields.get(LINK))
        if token:
            references[token] += 1
    for path in tracked_paths(md_root):
        match = ID_RE.match(Path(path).name)
        if match:
            paths[match.group(1)].append(path)

    changed_keys = set()
    removed = defaultdict(list)
    failures = 0
    for path in sorted(changed):
        match = ID_RE.match(Path(path).name)
        if match and match.group(1) in texts:
            key = match.group(1)
            changed_keys.add(key)
            if path not in paths.get(key, []):
                removed[key].append(path)
            if not paths.get(key):
                report.add("保留", file=path, key=key, major=texts[key], detail="仓库中没有替代 MD")
        elif not match:
            # index.md 之类没有编号的文件不对应表4 的任何一行，跳过即可，不算失败
            report.add("跳过", file=path, detail="文件名没有编号，不同步")
        elif Path(path).is_file():
            report.add("失败", file=path, key=match.group(1), detail="CSV 找不到对应编号，保留云盘文件")
            failures += 1
        else:
            report.add("保留", file=path, key=match.group(1) if match else "",
                       detail="仓库已删除，保留云盘文件")

    for key, options in sorted(paths.items()):
        if key not in texts:
            continue
        emit = partial(report.add, file="、".join(options), key=key, major=texts[key] or key)
        prior = [link_url(row.get("fields", {}).get(LINK)) for row in rows[key]]
        try:
            try:
                path = select_path(options, changed)
            except ValueError as error:
                if key not in changed_keys and len(rows[key]) == 1 and link_of(
                    rows[key][0].get("fields", {}).get(LINK)
                ):
                    emit("保留", old=prior[0], detail=str(error))
                    continue
                raise
            emit = partial(report.add, file=path, key=key, major=texts[key] or key)
            if len(rows[key]) != 1 and not (dry and not rows[key]):
                raise ValueError("找不到对应飞书记录" if not rows[key] else "飞书编号重复")
            record = rows[key][0] if rows[key] else None
            if sync_file(path, record, drive, app, tbl, domain, md_root,
                         compare_all or key in changed_keys, dry, emit, references, texts[key] or key, removed[key]) is False:
                failures += 1
        except (OSError, ValueError, SystemExit) as error:
            emit("失败", old="、".join(filter(None, prior)), detail=str(error))
            failures += 1
    if failures:
        sys.exit(f"{failures} 项 MD 同步失败")


if __name__ == "__main__":
    dry_run = os.environ.get("DRY_RUN", "").lower() == "true"
    with Report("md", dry_run).capture() as report:
        main(report, dry_run)
