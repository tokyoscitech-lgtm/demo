#!/usr/bin/env python3
"""把仓库里的 md 同步成飞书云文档（docx），表4「専攻」链接指向这篇文档。

- 链接已经是云文档：原地替换内容，链接不变
- 链接还是旧的 md 文件（或没有链接）：在同一个云盘文件夹里新建云文档，写入内容，
  换链接并读回确认后，把旧 md 文件移入回收站
仓库里仍然是 md，转换在同步时完成（飞书 Markdown → 文档块接口）。"""
import os
import re
import sys
import subprocess
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from sync_feishu import call, get_token, list_records, log  # noqa: E402

from sync_report import Report, scope_keys

KEY = "编号"                                       # 匹配键（纯文本，只读）
LINK = os.environ.get("LINK_FIELD", "専攻")       # md 链接所在的超链接字段
CSV_PATH = os.environ.get("CSV_PATH", "4_考学信息一览.csv")  # 链接字段没有文字时，取 CSV 的「専攻」作显示文字


def csv_texts():
    import csv

    with open(CSV_PATH, encoding="utf-8-sig", newline="") as stream:
        return {(row[KEY] or "").strip(): (row["専攻"] or "").strip()
                for row in csv.DictReader(stream)}


ID_RE = re.compile(r"^(\d{3}[A-Z]-\d{2})")


def nfc(s):
    """统一成 NFC：Mac 上传的文件名常是 NFD（ジ = シ + ゛），看起来一样但字节不同"""
    return unicodedata.normalize("NFC", s)


# ---------------------------------------------------------------- 云盘 API
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

    def trash(self, file_token, kind="file"):
        call("DELETE", f"/drive/v1/files/{file_token}", self.tok, query={"type": kind})


# ---------------------------------------------------------------- 表格链接
def link_url(value):
    segments = value if isinstance(value, list) else [value]
    return next((seg["link"] for seg in segments
                 if isinstance(seg, dict) and seg.get("link")), "")


def link_of(value):
    """链接指向的云盘 md 文件 token（旧方式）"""
    match = re.search(r"/file/([A-Za-z0-9]+)(?:[?#]|$)", link_url(value))
    return match.group(1) if match else None


def doc_of(value):
    """链接指向的飞书云文档 token"""
    match = re.search(r"/docx/([A-Za-z0-9]+)(?:[?#]|$)", link_url(value))
    return match.group(1) if match else None


def key_text(value):
    if isinstance(value, dict):
        return (value.get("text") or "").strip()
    if isinstance(value, list):
        return "".join(s.get("text", "") for s in value if isinstance(s, dict)).strip()
    return (value or "").strip()


def set_link(tok, app, tbl, record_id, text, url):
    """只写 LINK 字段（超链接），不碰「编号」。写入后读回确认；任何 API 错误都返回 False（由调用方保留旧文件）"""
    base = f"/bitable/v1/apps/{app}/tables/{tbl}/records/{record_id}"
    try:
        call("PUT", base, tok, body={"fields": {LINK: {"text": text, "link": url}}})
        got = call("GET", base, tok)
    except SystemExit as e:
        log(f"  ✗ 写入「{LINK}」链接出错：{e}")
        return False
    return link_url(got.get("record", {}).get("fields", {}).get(LINK)) == url


# ---------------------------------------------------------------- 云文档
DOC_PACE = 0.4       # 单篇文档编辑限频约 3 次/秒
MAX_BLOCKS = 1000    # 创建嵌套块接口单次最多插入 1000 个块


def convert(tok, markdown):
    """Markdown → 文档块。表格块的 merge_info 是只读字段，插入前必须去掉"""
    data = call("POST", "/docx/v1/documents/blocks/convert", tok,
                body={"content_type": "markdown", "content": markdown})
    blocks = data.get("blocks") or []
    for block in blocks:
        prop = (block.get("table") or {}).get("property")
        if isinstance(prop, dict):
            prop.pop("merge_info", None)
    return data.get("first_level_block_ids") or [], blocks


def batches(first_ids, blocks):
    """按一级块分批，每批（含所有子孙块）不超过 MAX_BLOCKS 个"""
    by_id = {b["block_id"]: b for b in blocks}

    def subtree(block_id):
        out, stack = [], [block_id]
        while stack:
            cur = by_id.get(stack.pop())
            if cur:
                out.append(cur)
                stack.extend(cur.get("children") or [])
        return out

    batch_ids, batch_blocks = [], []
    for block_id in first_ids:
        tree = subtree(block_id)
        if batch_ids and len(batch_blocks) + len(tree) > MAX_BLOCKS:
            yield batch_ids, batch_blocks
            batch_ids, batch_blocks = [], []
        batch_ids.append(block_id)
        batch_blocks.extend(tree)
    if batch_ids:
        yield batch_ids, batch_blocks


def root_children(tok, doc):
    data = call("GET", f"/docx/v1/documents/{doc}/blocks/{doc}", tok)
    return (data.get("block") or {}).get("children") or []


def write_doc(tok, doc, first_ids, blocks):
    """先在最前面插入新内容，再删掉后面的旧内容：中途出错时文档不会被清空"""
    old_count = len(root_children(tok, doc))
    rev = {"document_revision_id": -1}
    index = 0
    for ids, chunk in batches(first_ids, blocks):
        call("POST", f"/docx/v1/documents/{doc}/blocks/{doc}/descendant", tok, query=rev,
             body={"children_id": ids, "index": index, "descendants": chunk})
        index += len(ids)
        time.sleep(DOC_PACE)
    if old_count:
        call("DELETE", f"/docx/v1/documents/{doc}/blocks/{doc}/children/batch_delete", tok, query=rev,
             body={"start_index": index, "end_index": index + old_count})
        time.sleep(DOC_PACE)
    got = len(root_children(tok, doc))
    if got != len(first_ids):
        sys.exit(f"写入后读回不一致：应有 {len(first_ids)} 个一级块，实际 {got} 个")


def create_doc(tok, folder, title):
    data = call("POST", "/docx/v1/documents", tok, body={"folder_token": folder, "title": title})
    return data["document"]["document_id"]


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


def sync_doc(path, record, drive, app, tbl, domain, md_root, dry, emit, references, major, removed, key):
    rel_dir, _, name = nfc(path[len(md_root) + 1:]).rpartition("/")
    record_id = record["record_id"] if record else None
    value = record.get("fields", {}).get(LINK) if record else None
    old_url, doc, old_file = link_url(value), doc_of(value), link_of(value)

    first_ids, blocks = convert(drive.tok, Path(path).read_text(encoding="utf-8"))
    if not first_ids:
        raise ValueError("md 转换后没有内容")

    if doc:   # 已是云文档：原地替换内容，链接不变
        if not dry:
            write_doc(drive.tok, doc, first_ids, blocks)
        emit("修改", new=old_url, detail="将原地更新内容，链接不变" if dry else "内容已原地更新，链接不变")
        return

    # 还是旧 md 文件或没有链接：新建云文档，换链接后再处理旧文件
    action = "迁移" if old_file else "新增"
    folder = drive.folder(rel_dir)
    retired_folder = None
    if old_file and references[old_file] == 1:
        # 只回收能确认是本编号 md 的旧文件：在同一文件夹（或本次删除的 md 所在文件夹）里、文件名以编号开头
        folders = [folder] + [drive.folder(nfc(p[len(md_root) + 1:]).rpartition("/")[0]) for p in removed]
        for parent in filter(None, folders):
            if any(f.get("token") == old_file and f.get("type") == "file"
                   and nfc(f.get("name", "")).startswith(key) for f in drive.ls(parent)):
                retired_folder = parent
                break
    if dry:
        notes = ["将新建云文档并换链接"] + (["旧 md 文件将移入回收站"] if retired_folder else [])
        emit(action, old=old_url, detail="，".join(notes))
        return

    folder = folder or drive.folder(rel_dir, create=True)
    doc = create_doc(drive.tok, folder, Path(name).stem)
    time.sleep(DOC_PACE)
    try:
        write_doc(drive.tok, doc, first_ids, blocks)
    except SystemExit:
        try:   # 写入失败的新文档不留在云盘里；旧链接和旧文件都没动
            drive.trash(doc, "docx")
        except SystemExit:
            log(f"  ⚠ 写入失败的新文档 {doc} 未能移入回收站，请手动删除")
        raise
    new_url = f"{domain}/docx/{doc}"
    if not set_link(drive.tok, app, tbl, record_id, major, new_url):
        emit("失败", old=old_url, new=new_url, detail="云文档已建好，但写入或读回链接失败，旧文件保留")
        return False
    if old_file:
        references[old_file] -= 1
    if retired_folder:
        try:
            drive.trash(old_file)
        except SystemExit as error:
            emit("失败", old=old_url, new=new_url, detail=f"链接已换成云文档，但旧 md 文件移入回收站失败：{error}")
            return False
        drive.cache[retired_folder] = [f for f in drive.cache[retired_folder] if f.get("token") != old_file]
    emit(action, old=old_url, new=new_url,
         detail="已建成云文档" + ("，旧 md 文件已移入回收站" if retired_folder else ""))


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
            continue   # index.md 之类没有编号的文件不对应表4 的任何一行，不同步也不报告
        elif Path(path).is_file():
            report.add("失败", file=path, key=match.group(1), detail="CSV 找不到对应编号，保留云盘文件")
            failures += 1
        else:
            report.add("保留", file=path, key=match.group(1) if match else "",
                       detail="仓库已删除，保留云盘文件")

    scope = scope_keys()   # None = 全部；否则 = 本次改动的 md 和表4 行涉及的编号
    for key, options in sorted(paths.items()):
        if key not in texts or (scope is not None and key not in scope):
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
            if sync_doc(path, record, drive, app, tbl, domain, md_root, dry, emit,
                        references, texts[key] or key, removed[key], key) is False:
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
