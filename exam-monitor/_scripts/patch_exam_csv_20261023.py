# patch_exam_csv_20261023.py —— 修正总表「英語」列里「＋学内英語」的误判（幂等，可重复运行）
# 用法（在 demo 目录）：python3 exam-monitor/_scripts/patch_exam_csv_20261023.py exam-monitor/_scripts/exam_csv.py
# ENG：重写学内英语考试的判断：
#   - 认得「不另设/不自己出/不举行/不再有/取消…英语笔试」等否定写法；
#   - 「外部英语考试成绩」「英语外部考试」「其他英语考试」指的是外部考试，不算学内考试；
#   - 用外部成绩代替（必须交成绩、不设学内考试）的不算；可以用成绩代替或免考、但学内考试仍存在的，标「学内英語（成绩可免）」；
#   - 「不接受外部成绩代替」「全员参加」「英语包含在专门笔试里」算学内英语考试；
#   - 面接里用英语口述、又不交外部成绩的，标「面接内英語口述」；
#   - 只适用于别的专攻、往年、尚未核实、考试语言的句子不算；
#   - 第一句写“不用交英语成绩”时，后面若写明有学内英语考试或英语口述，不再直接判为「不要」。
# REV：英語列可用人工核对值覆盖（_scripts/.reviewed_eng.json，按笔记全文哈希匹配，笔记改过自动失效）。
# JIZEN：「事前連絡」列只看修士一般选拔，取值 必须／推荐／不要，外国人留学生特别选拔须事先联系而一般选拔不须时加「（留学生特别选拔必须）」（一般选拔里对外国人/外国学历/出口管理的联系要求算一般选拔「必须」）；笔记写“未确认”时留空、写“不募集”时显示不募集；没有「事前連絡要否」行的旧笔记可用 _scripts/.reviewed_jizen.json（同 REV 的哈希规则）。
# JP：「日本語」列可用人工核对值覆盖（_scripts/.reviewed_jp.json，笔记哈希一致时使用）；用于日语要求写在表格里、脚本读不出的笔记。
# AUDIT：update --columns 只更新非日期列时，不再因日期核对数据（.reviewed_dates.json）过期而中断。
# 输出最后一行：ALREADY／PATCHED <项>／ERROR <原因>。改之前备份为 exam_csv.py.bak-20261023（已有不覆盖）。
import sys, os, re, shutil
M = '# patched-20261023-'

HELPER = r'''# 判断一句话是否说明有学内英语考试（2026-10-23 修正）  # patched-20261023-ENG
# hard=必须考学内英语；soft=有学内英语考试但可用外部成绩代替/免考；oral=面接中用英语口述；None=不算
GK_RE=r'(英語|英语|外国語)[^。，,；;]{0,8}(筆記|筆答|試験を実施|笔试|考试)|学内.{0,4}英語|英語.{0,4}学力試験'
GK_EXT=r'其他的?(英语|英語)(考试|試験|检定|検定)|外部[^。，,；;]{0,6}(英语|英語)?(考试|試験|检定|検定|成绩|成績|スコア)|(英语|英語)(外部)?(能力)?的?(考试|試験|检定|検定)(的)?(成绩|成績|スコア|证明|証明)|(英语|英語)以外部'
GK_SKIP=r'其他\d?コース|其他[^。；;]{0,6}(コース|専攻|专攻|课程)|参考（|参考：|日语能力证明|日本語能力|只适用|不适用于本|与本専攻无关|与本专攻无关|无关|上次|曾有|曾提|过旧|不沿用|往年|是否|未能核实|考试语言|作答语言|答题语言'
GK_NEG=r'(没有|不设|不考|无|不另|不单|不再|不自己|不举行|不实施|不出|不用|取消|学内不|考场内不)[^，。；;]{0,14}(英语|英語)|(英语|英語)[^，。；;]{0,4}(不|没)|課さない|課していない|実施しない|行わない|実施せず|不接受|不受理|补交|補交|按\s*\d+\s*分'
GK_HARD=r'不能代替|不可代替|不得代替|不能免|不可免|没有[^。；;]{0,12}免考|没有[^。；;]{0,12}免除|不收[^。；;]{0,14}代替|所有人参加|全员参加|全員|必须参加|必考|都要考|(英语|英語)(包含在|含在|は)[^。；;]{0,10}(笔试|筆記)|含英语|含英語|含めて出題'
GK_HARD2=r'(不接受|不把|不将|不能用|不可用|不认可)[^。；;]{0,24}(代替|替代)'
GK_SOFT=r'(可以|可用|可选|也可|也能|可在)[^。；;]{0,60}(代替|替代|免考|免除|免笔试)|二选一|交不出|申请免考|符合条件[^。；;]{0,12}免|或参加|任选'
def gk_kind(s):
    if re.search(GK_SKIP,s): return None
    if re.search(GK_HARD2,s) and '英' in s: return 'hard'
    if re.search(r'(英語|英语)[^。，；;]{0,4}(口述|口頭|口试)|学内で英語の口述',s): return 'oral'
    s2=re.sub(GK_EXT,'',s)
    if not re.search(GK_RE,s2): return None
    if re.search(GK_HARD,s): return 'hard'
    if re.search(GK_NEG,s2): return None
    if re.search(GK_SOFT,s): return 'soft'
    if re.search(r'代替|替代|换算|代わり|免除|免考',s2): return None
    return 'hard'
def eng(txt, written=''):'''

E1 = "    if ss and re.search(r'不考英语|不考英語|不课英语|「英語」を課していない|英語を課さない',ss[0]): return '不要'\n"
E1N = E1  # 第一句写明“本専攻不考英语”的仍直接判为「不要」
E2 = "    if ss and re.search(NEG,ss[0]) and not re.search(r'TOEFL|TOEIC|IELTS',re.sub(r'(不需要|不要|无需)[^。；]*','',ss[0])): return '不要'\n"
E2N = "    has_gk={gk_kind(x) for x in ss[1:] if not re.search(r'^\\s*(变更点|変更点)|上一年度|上年度|去年|以前',x)}  # patched-20261023-ENG\n    if ss and re.search(NEG,ss[0]) and not re.search(r'TOEFL|TOEIC|IELTS',re.sub(r'(不需要|不要|无需)[^。；]*','',ss[0])) : return ('学内英語試験' if 'hard' in has_gk else '学内英語（成绩可免）') if has_gk&{'hard','soft'} else ('面接内英語口述' if 'oral' in has_gk else '不要')\n"
INIT = "    tests=[];mins=[];neg=unk=gakunai=False\n"
INITN = "    tests=[];mins=[];neg=unk=gakunai=False;hard=soft=oral=False\n"
GK_OLD = """        if re.search(r'(英語|英语|外国語)[^。，,；;]{0,8}(筆記|筆答|試験を実施|笔试|考试)|学内.{0,4}英語|英語.{0,4}学力試験',s) and not re.search(r'(没有|不设|不考|无)[^，。]{0,4}(英语|英語)|(英语|英語)[^，。]{0,4}(不|没)|免除|不接受|不受理|补交|補交|按\\s*\\d+\\s*分',s): gakunai=True  # patched-20261022-P2
    if re.search(r'外国語（英語）|英語（筆記）|英語\\s*\\d+:\\d+|英語[:：]?\\s*筆記',written): gakunai=True
    if tests:
        o='/'.join(tests)+'提出'+(f'（{"・".join(mins[:3])}）' if mins else '')
        if gakunai: o+='＋学内英語'
        return o
    if gakunai: return '学内英語試験'
"""
GK_NEW = """        g=gk_kind(s)  # patched-20261023-ENG
        if g=='hard': hard=True
        elif g=='soft': soft=True
        elif g=='oral': oral=True
    for ln in written.split('\\n'):
        if re.search(r'外国語（英語）|英語（筆記）|英語\\s*\\d+:\\d+|英語[:：]?\\s*筆記',ln):
            if re.search(r'免除|免考|で免|可免',ln): soft=True
            else: hard=True
    gakunai=hard or soft
    tag='学内英語' if hard else '学内英語（成绩可免）'
    if tests:
        o='/'.join(tests)+'提出'+(f'（{"・".join(mins[:3])}）' if mins else '')
        if gakunai: o+='＋'+tag
        return o
    if gakunai: return '学内英語試験' if hard else tag
    if oral: return '面接内英語口述'
"""
A1 = 'def reviewed_dates(t,p):\n    """Read date audit data kept outside the user\'s Markdown notes."""\n'
A1N = 'AUDIT_OFF=False  # 只更新非日期列时不需要日期核对数据  # patched-20261023-AUDIT\n' + A1 + '    if AUDIT_OFF: return None\n'
A2 = "    selected=[c.strip() for c in a.columns.split(',') if c.strip()]\n"
A2N = A2 + "    global AUDIT_OFF; AUDIT_OFF=bool(selected) and not (set(selected)&set(DATE_COLUMNS))  # patched-20261023-AUDIT\n"

# ---- REV：英語列人工核对值（.reviewed_eng.json） ----
R1 = "    r['英語']=eng('\\n'.join(bullets(eng_full)), '\\n'.join(raw['筆記試験'])+wr)\n"
R1N = R1 + "    rv=reviewed_eng(t,p)  # patched-20261023-REV\n    if rv is not None: r['英語']=rv\n"
R2 = "def build_row(p, idx):"
R2N = """def reviewed_eng(t,p):  # patched-20261023-REV
    # 英語列的人工核对值存在 _scripts/.reviewed_eng.json；笔记全文哈希一致时才使用，笔记改过就回到自动解析
    cachep=os.path.join(os.path.dirname(os.path.abspath(__file__)),'.reviewed_eng.json')
    try:
        with open(cachep,encoding='utf-8') as f: cache=json.load(f)
    except Exception: return None
    ident=re.match(r'\\d{3}[A-Z]-\\d{2}(?:-\\d+)?',os.path.basename(p))
    item=cache.get('entries',{}).get(ident.group() if ident else '')
    if not item or item.get('source_sha256')!=hashlib.sha256(t.encode('utf-8')).hexdigest(): return None
    return item.get('英語')
def build_row(p, idx):"""

# ---- JIZEN：事前連絡收成 必须/推荐/不要（可带「（留学生必须）」），未确认留空；无该行的旧笔记可用 .reviewed_jizen.json ----
J1 = """    lead=re.match(r'(?:事前(?:連絡|联系)要否[:：]\\s*)?(必须|必須|推荐|推奨|不要|无需)',txt)
    if lead: return {'必须':'必须','必須':'必须','推荐':'推荐','推奨':'推荐'}.get(lead.group(1),'不要')
"""
J1N = """    lead=re.match(r'(?:事前(?:連絡|联系)要否[:：]\\s*)?(必须|必須|推荐|推奨|不要|无需)(（留学生必须）)?',txt)  # patched-20261023-JIZEN
    if lead: return {'必须':'必须','必須':'必须','推荐':'推荐','推奨':'推荐'}.get(lead.group(1),'不要')+(lead.group(2) or '')
    if re.match(r'(?:事前(?:連絡|联系)要否[:：]\\s*)?(未确认|未確認)',txt): return ''
"""
J2 = "    r['事前連絡_s']=jizen(jl)\n"
J2N = J2 + "    rj=reviewed_col(t,p,'.reviewed_jizen.json','事前連絡')  # patched-20261023-JIZEN\n    if rj is not None: r['事前連絡_s']=rj\n"
J3 = "def build_row(p, idx):"
J3N = """def reviewed_col(t,p,fname,key):  # patched-20261023-JIZEN
    # 某列的人工核对值（_scripts/<fname>），笔记全文哈希一致时才使用
    cachep=os.path.join(os.path.dirname(os.path.abspath(__file__)),fname)
    try:
        with open(cachep,encoding='utf-8') as f: cache=json.load(f)
    except Exception: return None
    ident=re.match(r'\\d{3}[A-Z]-\\d{2}(?:-\\d+)?',os.path.basename(p))
    item=cache.get('entries',{}).get(ident.group() if ident else '')
    if not item or item.get('source_sha256')!=hashlib.sha256(t.encode('utf-8')).hexdigest(): return None
    return item.get(key)
def build_row(p, idx):"""

# ---- JIZEN2：后缀改为「（留学生特别选拔必须）」（说明是那条特别选拔的要求，不是说留学生走一般选拔也必须）----
K1 = "(必须|必須|推荐|推奨|不要|无需)(（留学生必须）)?',txt)  # patched-20261023-JIZEN\n"
K1N = "(必须|必須|推荐|推奨|不要|无需)(（留学生(?:特别选拔)?必须）)?',txt)  # patched-20261023-JIZEN  # patched-20261023-JIZEN2\n"

# ---- JIZEN3：本年度不招修士时写「不募集」 ----
L1 = "(必须|必須|推荐|推奨|不要|无需)(（留学生(?:特别选拔)?必须）)?',txt)  # patched-20261023-JIZEN  # patched-20261023-JIZEN2\n"
L1N = "(必须|必須|推荐|推奨|不要|无需|不募集)(（留学生(?:特别选拔)?必须）)?',txt)  # patched-20261023-JIZEN  # patched-20261023-JIZEN2  # patched-20261023-JIZEN3\n"
L2 = "{'必须':'必须','必須':'必须','推荐':'推荐','推奨':'推荐'}.get(lead.group(1),'不要')+(lead.group(2) or '')"
L2N = "{'必须':'必须','必須':'必须','推荐':'推荐','推奨':'推荐','不募集':'不募集'}.get(lead.group(1),'不要')+(lead.group(2) or '')"

# ---- JP：「日本語」列可用人工核对值覆盖（_scripts/.reviewed_jp.json，按笔记哈希匹配） ----
P1 = "    r['日本語']=jpn('\\n'.join(bullets(jp)))\n"
P1N = P1 + "    rjp=reviewed_col(t,p,'.reviewed_jp.json','日本語')  # patched-20261023-JP\n    if rjp is not None: r['日本語']=rjp\n"

def main(path):
    t = open(path, encoding='utf-8').read()
    plan = []
    if M + 'ENG' not in t:
        plan.append(('ENG', [("def eng(txt, written=''):", HELPER), (INIT, INITN), (E1, E1N), (E2, E2N), (GK_OLD, GK_NEW)]))
    if M + 'AUDIT' not in t:
        plan.append(('AUDIT', [(A1, A1N), (A2, A2N)]))
    if M + 'REV' not in t:
        plan.append(('REV', [(R1, R1N), (R2, R2N)]))
    if M + 'JIZEN' not in t:
        plan.append(('JIZEN', [(J1, J1N), (J2, J2N), (J3, J3N)]))
    if M + 'JIZEN2' not in t:
        plan.append(('JIZEN2', [(K1, K1N)]))
    if M + 'JIZEN3' not in t:
        plan.append(('JIZEN3', [(L1, L1N), (L2, L2N)]))
    if M + 'JP' not in t:
        plan.append(('JP', [(P1, P1N)]))
    if not plan:
        print('ALREADY 全部补丁已打，未改动'); return 0
    if M + 'ENG' not in t and '# patched-20261022-P2' not in t:
        print('ERROR 需要先运行 patch_exam_csv_20261022.py（缺 P2）；未改动'); return 2
    for pid, reps in plan:
        for old, new in reps:
            if t.count(old) != 1:
                print('ERROR 找不到原片段（' + pid + '）：' + old.strip()[:40] + '；未改动'); return 2
    for pid, reps in plan:
        for old, new in reps: t = t.replace(old, new, 1)
    compile(t, path, 'exec')
    bak = path + '.bak-20261023'
    if not os.path.exists(bak): shutil.copy2(path, bak)
    open(path, 'w', encoding='utf-8').write(t)
    print('PATCHED ' + ','.join(p for p, _ in plan) + '（备份：' + bak + '）'); return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else 'exam-monitor/_scripts/exam_csv.py'))
