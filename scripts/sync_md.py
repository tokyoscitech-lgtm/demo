#!/usr/bin/env python3
"""只把「本次 commit 改动过的」exam-monitor/ 下的 md 同步到飞书云盘，并维护多维表格「编号」里的文件链接。

改动文件列表由 workflow 用 git diff 生成（MD_CHANGED_LIST 指向的文本文件，一行一个路径）。
对列表里的每个 md：
- 仓库里还在：
  - 云盘里有、内容一样：不动（只校对「编号」链接）
  - 云盘里有、内容不同：上传新文件 -> 把「编号」链接指向新文件 -> 确认成功后把旧文件移到回收站
    （飞书 API 不能覆盖已上传文件，只能换新文件，所以 file token 会变）
  - 云盘里没有：按 大学/研究科/ 结构建文件夹并上传，再补上「编号」链接
- 仓库里已删除：只记日志，不删除云盘文件
列表以外的 md 一律不碰。只用 Python 标准库。

环境变量：
  FEISHU_APP_ID / FEISHU_APP_SECRET   必填
  FEISHU_APP_TOKEN / FEISHU_TABLE_ID  多维表格
  FEISHU_FOLDER_TOKEN                 云盘根文件夹（对应仓库 exam-monitor/）
  FEISHU_DOMAIN                       如 https://ccn3ynd15sp4.feishu.cn
  MD_ROOT                             默认 exam-monitor
  MD_CHANGED_LIST                     改动文件列表路径（必填）
  DRY_RUN                             "true" 时只打印计划
"""
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from sync_feishu import API, call, get_token, list_records, log  # noqa: E402

KEY = "编号"
ID_RE = re.compile(r"^(\d{3}[A-Z]-\d{2})")


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
def link_of(value):
    """从「编号」（超链接字段 {"text","link"}，或旧的文本+链接段）里取 file token"""
    segs = [value] if isinstance(value, dict) else (value if isinstance(value, list) else [])
    for seg in segs:
        if isinstance(seg, dict) and seg.get("link"):
            m = re.search(r"/file/([A-Za-z0-9]+)", seg["link"])
            if m:
                return m.group(1)
    return None


def key_text(value):
    if isinstance(value, dict):
        return (value.get("text") or "").strip()
    if isinstance(value, list):
        return "".join(s.get("text", "") for s in value if isinstance(s, dict)).strip()
    return (value or "").strip()


def set_link(tok, app, tbl, record_id, key, file_token, domain):
    """「编号」须为超链接字段。写入后读回确认；任何 API 错误都返回 False（由调用方回滚）"""
    base = f"/bitable/v1/apps/{app}/tables/{tbl}/records/{record_id}"
    try:
        call("PUT", base, tok, body={"fields": {KEY: {"text": key, "link": f"{domain}/file/{file_token}"}}})
        got = call("GET", base, tok)
    except SystemExit as e:
        log(f"  ✗ 写入「编号」链接出错：{e}")
        return False
    return link_of(got.get("record", {}).get("fields", {}).get(KEY)) == file_token


# ---------------------------------------------------------------- 主流程
def main():
    env = os.environ
    app_id, app_secret = env.get("FEISHU_APP_ID"), env.get("FEISHU_APP_SECRET")
    app, tbl = env.get("FEISHU_APP_TOKEN"), env.get("FEISHU_TABLE_ID")
    root_folder, domain = env.get("FEISHU_FOLDER_TOKEN"), (env.get("FEISHU_DOMAIN") or "").rstrip("/")
    md_root = env.get("MD_ROOT", "exam-monitor").rstrip("/")
    list_path = env.get("MD_CHANGED_LIST")
    dry = env.get("DRY_RUN", "").lower() == "true"
    if not all([app_id, app_secret, app, tbl, root_folder, domain, list_path]):
        sys.exit("缺少环境变量")

    changed = []
    for line in Path(list_path).read_text(encoding="utf-8").splitlines():
        p = line.strip()
        if p.startswith(md_root + "/") and p.endswith(".md") and p not in changed:
            changed.append(p)
    log(f"本次改动的 md：{len(changed)} 个")
    for p in changed:
        log(f"  {p}")
    if not changed:
        log("没有 md 改动，跳过。")
        return

    tok = get_token(app_id, app_secret)
    drive = Drive(tok, root_folder)

    by_key = {}
    for rec in list_records(tok, app, tbl, [KEY]):
        v = rec.get("fields", {}).get(KEY)
        text = key_text(v)
        if text:
            by_key.setdefault(text, (rec["record_id"], link_of(v)))

    stats = {"替换": [], "新上传": [], "内容相同": [], "仓库已删除(未删云盘)": [],
             "修正链接": [], "表格无此编号": [], "失败": []}

    for path in changed:
        rel = nfc(path[len(md_root) + 1:])
        rel_dir, _, name = rel.rpartition("/")
        local = Path(path)
        m = ID_RE.match(name)
        key = m.group(1) if m else None
        rec = by_key.get(key) if key else None

        if not local.exists():
            stats["仓库已删除(未删云盘)"].append(rel)
            log(f"  - 仓库已删除，云盘保留：{rel}")
            continue

        content = local.read_bytes()
        folder = drive.folder(rel_dir)
        olds = drive.files(folder, name) if folder else []
        if len(olds) > 1:
            log(f"  ⚠ 云盘同名文件 {len(olds)} 个：{rel}（链接指向的那个视为当前文件，其余不动）")
        old = olds[0] if olds else None
        if rec and rec[1] in olds:
            old = rec[1]

        if old and drive.download(old) == content:
            stats["内容相同"].append(rel)
            if rec and rec[1] != old:
                log(f"  链接待修正 {key} -> {rel}")
                if not dry:
                    (stats["修正链接"] if set_link(tok, app, tbl, rec[0], key, old, domain)
                     else stats["失败"]).append(rel)
            else:
                log(f"  = 内容相同 {rel}")
            continue

        action = "替换" if old else "新上传"
        if dry:
            log(f"  · 将{action} {rel}" + ("" if rec or not key else "（表格无此编号，不改链接）"))
            stats[action].append(rel)
            continue

        folder = folder or drive.folder(rel_dir, create=True)
        try:
            new = drive.upload(folder, name, content)
        except SystemExit as e:
            stats["失败"].append(rel)
            log(f"  ✗ 上传失败：{rel}：{e}")
            continue
        if rec:
            if not set_link(tok, app, tbl, rec[0], key, new, domain):
                try:
                    drive.trash(new)  # 回滚：保留旧文件和旧链接
                except SystemExit:
                    log(f"  ⚠ 回滚时删除新文件失败，请手动删除云盘里多出的同名文件：{rel}")
                stats["失败"].append(rel)
                log(f"  ✗ 链接写入失败，已撤回新文件：{rel}")
                continue
            by_key[key] = (rec[0], new)
        elif key:
            stats["表格无此编号"].append(rel)
        for t in olds:  # 旧版本（含以前失败遗留的同名文件）全部移到回收站
            try:
                drive.trash(t)
            except SystemExit as e:
                log(f"  ⚠ 旧文件移到回收站失败（不影响链接）：{rel}：{e}")
        stats[action].append(rel)
        log(f"  ✓ {action} {rel}")

    log("结果：" + "，".join(f"{k} {len(v)}" for k, v in stats.items() if v))
    if dry:
        log("DRY_RUN=true，不写入。")

    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### md 同步{'（试运行）' if dry else ''}：本次改动 {len(changed)} 个\n\n")
            f.write("| " + " | ".join(stats) + " |\n|" + "---|" * len(stats) + "\n| " +
                    " | ".join(str(len(v)) for v in stats.values()) + " |\n\n")
            for k, v in stats.items():
                if v:
                    f.write(f"<details><summary>{k}</summary>\n\n" +
                            "".join(f"- `{x}`\n" for x in v) + "\n</details>\n\n")
    if stats["失败"]:
        sys.exit(f"{len(stats['失败'])} 个文件处理失败")


if __name__ == "__main__":
    main()
