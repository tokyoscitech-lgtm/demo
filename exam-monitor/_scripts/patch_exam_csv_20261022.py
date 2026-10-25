# patch_exam_csv_20261022.py —— 给 exam_csv.py 打解析修正（幂等，可重复运行；每一项单独判断是否已打）
# 用法（在 demo 目录）：python3 exam-monitor/_scripts/patch_exam_csv_20261022.py exam-monitor/_scripts/exam_csv.py
# P1：日程表格子里的来源括号（如「（2026/3/23 予告）」「（入试日程页）」）不再被当成出願/考试日期。
# P2：英語小节里「英语按0分，不接受考试当天补交」这类句子不再被误判为学内英语考试。
# P3：认得 #### 小标题（「### 語学」下的「#### 英語要件」「#### 日本語要件」）；##、### 的切分方式与原来完全相同。
# P6：写表时沿用表里已有的列顺序、保留用户自己加的列；P7：update 时保留该行自加列的值。
#（曾有的 P4/P5「研究方向」列已于 2026-10-23 按用户要求取消；已打过 P5 的脚本里留下的 research_kw 函数不影响总表。）
# 只做精确片段替换。某项既没有已打标记、原片段也找不到时，整个文件一项都不改并报错退出。改之前先备份为 exam_csv.py.bak-20261022（已有备份不覆盖）。
# 输出最后一行：ALREADY（全部已打）／PATCHED <本次打上的项>／ERROR <原因>。
import sys, shutil, os, re

M = '# patched-20261022-'
PIECES = [
    ('P1a',
     "def build_row(p, idx):",
     "# 日程表格子末尾的来源注明（予告、订正、某某页等）只是出处，括号里的日期不是出願/考试日  " + M + "P1a\n"
     "SRCNOTE=re.compile(r'[（(][^（）()]*?(?:予告|告知|お知らせ|訂正|订正|公告|公表|掲載|页|ページ|資料|资料|说明会|説明会|发布|公布)[^（）()]*[）)]')\n"
     "def strip_src(v): return SRCNOTE.sub('',v)\n"
     "def build_row(p, idx):"),
    ('P1b',
     "                sd[cat].append(pre+txt); raw[cat].append(lab+'：'+val); break",
     "                sd[cat].append(pre+txt); raw[cat].append(lab+'：'+strip_src(val)); break  " + M + "P1b"),
    ('P2',
     "        if re.search(r'(英語|英语|外国語)[^。]{0,8}(筆記|筆答|試験を実施|笔试|考试)|学内.{0,4}英語|英語.{0,4}学力試験',s) and not re.search(r'(没有|不设|不考|无)[^，。]{0,4}(英语|英語)|(英语|英語)[^，。]{0,4}(不|没)|免除',s): gakunai=True",
     "        if re.search(r'(英語|英语|外国語)[^。，,；;]{0,8}(筆記|筆答|試験を実施|笔试|考试)|学内.{0,4}英語|英語.{0,4}学力試験',s) and not re.search(r'(没有|不设|不考|无)[^，。]{0,4}(英语|英語)|(英语|英語)[^，。]{0,4}(不|没)|免除|不接受|不受理|补交|補交|按\\s*\\d+\\s*分',s): gakunai=True  " + M + "P2"),
    ('P3',
     "def section(t,pat):\n    m=re.search(r'^(#{2,3}) [^\\n]*'+pat+r'[^\\n]*\\n(.*?)(?=^#{2,3} |\\Z)',t,re.S|re.M)\n    return m.group(2) if m else ''",
     "def section(t,pat):  " + M + "P3\n"
     "    # ##/### 标题：与原来相同，到下一个 ## 或 ### 为止；#### 标题：到下一个 ##～#### 为止\n"
     "    m=re.search(r'^(#{2,4}) [^\\n]*'+pat+r'[^\\n]*\\n',t,re.M)\n"
     "    if not m: return ''\n"
     "    lv=max(len(m.group(1)),3); rest=t[m.end():]\n"
     "    e=re.search(r'^#{2,%d} '%lv,rest,re.M)\n"
     "    return rest[:e.start()] if e else rest"),
    ('P6',
     "def write_csv(path,rows):\n    enrich(path,rows)\n    tmp=path+'.tmp'\n    with open(tmp,'w',encoding='utf-8-sig',newline='') as f:\n        w=csv.DictWriter(f,fieldnames=COLS,extrasaction='ignore'); w.writeheader(); w.writerows(rows)\n    os.replace(tmp,path)",
     "def write_csv(path,rows):  " + M + "P6\n"
     "    enrich(path,rows)\n"
     "    fields=list(COLS)\n"
     "    try:\n"
     "        with open(path,encoding='utf-8-sig',newline='') as f: head=next(csv.reader(f),[])\n"
     "        head=[OLD_COLS.get(c,c) for c in head if c]\n"
     "        if set(COLS)<=set(head): fields=head  # 用户调整过列顺序：沿用表里的顺序（含自己加的列）\n"
     "        else: fields=list(COLS)+[c for c in head if c not in COLS]  # 新标准列按默认位置插入，用户自加的列放最后\n"
     "    except Exception: pass\n"
     "    tmp=path+'.tmp'\n"
     "    with open(tmp,'w',encoding='utf-8-sig',newline='') as f:\n"
     "        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore'); w.writeheader(); w.writerows(rows)\n"
     "    os.replace(tmp,path)"),
    ('P7',
     "                rows[pos[k]]=r; print('UPDATED',rel)",
     "                for c,v in old.items(): r.setdefault(c,v)  # 用户自加的列原样保留  " + M + "P7\n"
     "                rows[pos[k]]=r; print('UPDATED',rel)"),
]

def main(path):
    t = open(path, encoding='utf-8').read()
    # 兼容上一版补丁（标记为不带编号的「# patched-20261022」，包含 P1a/P1b/P2 三项）
    legacy = {'P1a', 'P1b', 'P2'} if re.search(r'# patched-20261022(?!-)', t) else set()
    todo, bad = [], []
    for pid, old, new in PIECES:
        if M + pid in t or pid in legacy: continue
        if t.count(old) == 1: todo.append((pid, old, new))
        else: bad.append(pid)
    if bad:
        print('ERROR 找不到原片段（脚本可能已被改过）：' + '、'.join(bad) + '；未改动'); return 2
    if not todo:
        print('ALREADY 全部补丁已打，未改动'); return 0
    for _, old, new in todo: t = t.replace(old, new)
    compile(t, path, 'exec')
    bak = path + '.bak-20261022'
    if not os.path.exists(bak): shutil.copy2(path, bak)
    open(path, 'w', encoding='utf-8').write(t)
    print('PATCHED ' + ','.join(p for p, _, _ in todo) + '（备份：' + bak + '）'); return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'exam-monitor/_scripts/exam_csv.py'))
