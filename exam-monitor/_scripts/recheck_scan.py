# recheck_scan.py v3 —— 复查模式选目标（只读扫描 md；只写 _scripts/.recheck_claims.json）
# 用法（在 demo 目录）：python3 exam-monitor/_scripts/recheck_scan.py pick [--n 25] [--claim]
#                       python3 exam-monitor/_scripts/recheck_scan.py done <md相对路径...> [--faculty-unresolved]
#                       python3 exam-monitor/_scripts/recheck_scan.py stats
import os, re, sys, json, datetime, argparse
ROOT = 'exam-monitor'
SD = os.path.join(ROOT, '_scripts')
sys.path.insert(0, SD)
try:
    import exam_csv as X
except Exception:
    X = None
STATE = os.path.join(SD, '.recheck_state.json')
CLAIMS = os.path.join(SD, '.recheck_claims.json')
PENDING_DAYS, FAC_UNRES_DAYS, REFRESH_DAYS, RM_DAYS = 5, 30, 45, 60

def jload(p):
    try: return json.load(open(p, encoding='utf-8'))
    except Exception: return {}
def jsave(p, d):
    tmp = p + '.tmp'; json.dump(d, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=0); os.replace(tmp, p)

def md_list():
    if X:
        return X.md_files(os.path.abspath(ROOT))
    out = []
    for dp, _, fs in os.walk(ROOT):
        for f in fs:
            if f.endswith('.md') and f != 'index.md' and not dp.startswith(SD):
                out.append(os.path.relpath(os.path.join(dp, f), ROOT))
    return sorted(out)

def section(t, head):
    m = re.search(r'^###\s*' + re.escape(head) + r'.*?$(.*?)(?=^##\s|^###\s|\Z)', t, re.M | re.S)
    return m.group(1) if m else ''

def faculty_lines(t):
    s = section(t, '研究室 / 指導教員')
    ls = []
    for l in s.splitlines():
        st = l.strip()
        if not st.startswith('- '): continue
        body = st[2:]
        if body.startswith(('事前連絡要否', '说明')): continue
        if re.search(r'[：:]\s*$', body) and '｜' not in body: continue  # 分组标题行
        ls.append(body)
    return ls

def fac_ok(b):
    if '本年度不募集' in b and '｜' in b: return True
    has_dir = b.count('｜') >= 2
    has_lab = bool(re.search(r'\]\((?!https?://researchmap)[^)]*https?://', b)) or '研究室页官方未公开' in b
    return has_dir and has_lab

def old_layout(t):
    # 旧版式：日程表还有「来源/最后确认」列，或链接列表超过 10 条
    sched = re.search(r'## 📋.*?(?=\n## )', t, re.S)
    if sched and '最后确认' in sched.group(0): return True
    m = re.search(r'^# .*$', t, re.M)
    if m:
        body = t[m.end():]; e = re.search(r'^## ', body, re.M)
        head = body[:e.start()] if e else body
        if len(re.findall(r'^[-*]\s*\[', head, re.M)) > 10: return True
    return False

def scan(rel):
    p = os.path.join(ROOT, rel)
    try: t = open(p, encoding='utf-8').read()
    except Exception: return {'rel': rel, 'kind': 'UNREADABLE'}
    if '最新更新：（未抓取）' in t: return {'rel': rel, 'kind': '骨架'}
    if re.match(r'\s*(<!--.*?-->\s*)?---\n', t[:3000], re.S): return {'rel': rel, 'kind': '旧格式'}
    sched = re.search(r'## 📋.*?(?=\n## )', t, re.S)
    sched = sched.group(0) if sched else ''
    pend = sched.count('⏳') + sched.count('❓') + len(re.findall(r'未公布', sched))
    fl = faculty_lines(t)
    bad = [b for b in fl if not fac_ok(b)]
    rm_missing = sum(1 for b in fl if '本年度不募集' not in b and 'researchmap.jp' not in b)
    u = re.search(r'最新更新[:：]\s*(\d{4}-\d{2}-\d{2})', t)
    return {'rel': rel, 'kind': '范本', 'pending': pend, 'fac_total': len(fl), 'fac_bad': len(bad) if fl else -1,
            'updated': u.group(1) if u else '', 'old_layout': old_layout(t), 'rm_missing': rm_missing}

def days_since(iso):
    if not iso: return 9999
    try: return (datetime.datetime.now() - datetime.datetime.fromisoformat(iso)).total_seconds() / 86400
    except Exception: return 9999

def in_use_filter(rels):
    if not X: return rels, None
    try:
        use, _ = X.load_master(os.getcwd())
    except Exception:
        use = None
    excl = set()
    try: excl = X.excluded_program_ids()
    except Exception: pass
    ids = {r['専攻编号'] for r in use} if use else None
    out = []
    for r in rels:
        no = X.md_id(r)
        if no in excl: continue
        if ids is not None and no not in ids: continue
        out.append(r)
    return out, ids

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['pick', 'done', 'stats'])
    ap.add_argument('paths', nargs='*')
    ap.add_argument('--n', type=int, default=25)
    ap.add_argument('--claim', action='store_true')
    ap.add_argument('--claim-hours', type=float, default=0.9)
    ap.add_argument('--faculty-unresolved', action='store_true')
    a = ap.parse_args()
    st = jload(STATE)
    now = datetime.datetime.now().isoformat(timespec='minutes')
    if a.cmd == 'done':
        for rel in a.paths:
            r = scan(rel); e = st.get(rel, {})
            e['checked'] = now; e['rm_checked'] = now
            if r.get('kind') == '范本' and r['fac_bad'] == 0: e['faculty_done'] = True
            if a.faculty_unresolved: e['faculty_unresolved'] = now
            st[rel] = e; print('DONE', rel, r)
        jsave(STATE, st); return
    rels = [r for r in md_list() if not r.endswith('_冬入試.md')]
    rels, _ = in_use_filter(rels)
    claims = {k: v for k, v in jload(CLAIMS).items() if days_since(v) * 24 < a.claim_hours}
    rows, kinds = [], {}
    for rel in rels:
        r = scan(rel); kinds[r['kind']] = kinds.get(r['kind'], 0) + 1
        if r['kind'] != '范本' or rel in claims: continue
        e = st.get(rel, {}); last = days_since(e.get('checked'))
        reasons, pr = [], None
        fac_need = r['fac_bad'] != 0 and days_since(e.get('faculty_unresolved')) > FAC_UNRES_DAYS
        if r['pending'] and last >= PENDING_DAYS:
            reasons.append(f'待公布{r["pending"]}处'); pr = 0
        if fac_need and last >= 1:
            reasons.append('教员无条目' if r['fac_bad'] < 0 else f'教员不全{r["fac_bad"]}/{r["fac_total"]}')
            pr = 1 if pr is None else pr
        if r['old_layout'] and last >= 1:
            reasons.append('版式待精简'); pr = 1 if pr is None else pr
        if r['rm_missing'] and days_since(e.get('rm_checked')) >= RM_DAYS:
            reasons.append(f'补researchmap{r["rm_missing"]}行'); pr = 2 if pr is None else pr
        if not reasons and last >= REFRESH_DAYS and days_since(r['updated'] + 'T00:00' if r['updated'] else '') >= REFRESH_DAYS:
            reasons.append('定期全面复核'); pr = 2
        if reasons: rows.append((pr, X.md_id(rel) if X else rel, rel, reasons))
    if a.cmd == 'stats':
        print('KINDS', kinds); print('CANDIDATES', len(rows))
        for p in (0, 1, 2): print(f'P{p}', sum(1 for x in rows if x[0] == p))
        return
    rows.sort(key=lambda x: (x[0], x[1]))
    # 按研究科成组取：从最高优先的研究科开始，整组取，累计到约 n 个
    grp = lambda no: (re.match(r'(\d{3}[A-Z]+)', no) or re.match(r'(.*)', no)).group(1)
    order, seen = [], set()
    for x in rows:
        g = grp(x[1])
        if g not in seen: seen.add(g); order.append(g)
    out = []
    for g in order:
        members = [x for x in rows if grp(x[1]) == g]
        if out and len(out) + len(members) > a.n: continue
        out += members
        if len(out) >= a.n: break
    print(f'RECHECK\t本轮{len(out)}个／候选{len(rows)}个\t骨架{kinds.get("骨架",0)}·旧格式{kinds.get("旧格式",0)}（复查不处理）')
    if not out: print('NOTHING 当前没有需要复查的专攻')
    for p, no, rel, rs in out: print(f'{rel}\tP{p}\t' + ','.join(rs))
    if a.claim and out:
        c = jload(CLAIMS); c = {k: v for k, v in c.items() if days_since(v) * 24 < a.claim_hours}
        for _, _, rel, _ in out: c[rel] = datetime.datetime.now().isoformat(timespec='seconds')
        jsave(CLAIMS, c)

if __name__ == '__main__':
    main()
