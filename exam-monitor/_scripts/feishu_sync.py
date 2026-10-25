#!/usr/bin/env python3
"""feishu_sync.py — 把 4_考学信息一览.csv 的行同步（upsert）到飞书多维表格。
配置：demo/.feishu.json {app_id, app_secret, app_token, table_id, base_url}
用法（在 demo 目录）：
  python3 exam-monitor/_scripts/feishu_sync.py --all              # 全量同步 706 行
  python3 exam-monitor/_scripts/feishu_sync.py <md相对路径> ...     # 只同步这些専攻（本轮 update 过的）
  python3 exam-monitor/_scripts/feishu_sync.py --limit 2 --all      # 小测试：只同步前 2 行
  加 --dry-run 只打印将发送的 JSON，不联网
匹配键：主键 = 大学|研究科|専攻（表格首列）。已有则 batch_update，没有则 batch_create。
"""
import json,csv,os,sys,argparse,urllib.request,urllib.error,time,unicodedata
N=lambda x:unicodedata.normalize('NFC',x or '')
COLS=['编号','大学','研究科','専攻','课程','领域','春夏_出願','春夏_試験','秋冬_出願','秋冬_試験','英語','日本語','筆記内容','口述/面接内容','過去問','事前連絡','定員/倍率']
KEY='主键'
RENAME={'夏_出願':'春夏_出願','夏_試験':'春夏_試験','冬_出願':'秋冬_出願','冬_試験':'秋冬_試験'}  # 2026-10-18 列名变更：飞书里旧字段自动改名为新字段（保留原有数据）
def pk(r): return '|'.join(N(r[c]) for c in ('大学','研究科','専攻'))
class Feishu:
    def __init__(s,cfg,dry=False):
        s.c=cfg; s.dry=dry; s.base=cfg.get('base_url','https://open.feishu.cn').rstrip('/'); s.tok=None
        s.tbl=f"/open-apis/bitable/v1/apps/{cfg['app_token']}/tables/{cfg['table_id']}"
    def req(s,method,path,body=None,auth=True):
        if s.dry and path!='/open-apis/auth/v3/tenant_access_token/internal':
            print('DRY',method,path); print(json.dumps(body,ensure_ascii=False,indent=1)[:3000]); return {'code':0,'data':{}}
        h={'Content-Type':'application/json; charset=utf-8'}
        if auth: h['Authorization']='Bearer '+s.token()
        data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None
        for i in range(4):
            try:
                with urllib.request.urlopen(urllib.request.Request(s.base+path,data=data,headers=h,method=method),timeout=60) as r: d=json.load(r)
            except urllib.error.HTTPError as e:
                d=json.loads(e.read() or b'{}') or {'code':e.code,'msg':str(e)}
            if d.get('code')==0: return d
            if d.get('code') in (1254290,1254291,99991400) and i<3: time.sleep(2*(i+1)); continue  # 频控/冲突重试
            raise SystemExit(f"飞书 API 错误 {method} {path}: code={d.get('code')} msg={d.get('msg')}")
    def token(s):
        if s.tok: return s.tok
        if s.dry: s.tok='DRY'; return s.tok
        d=s.req('POST','/open-apis/auth/v3/tenant_access_token/internal',{'app_id':s.c['app_id'],'app_secret':s.c['app_secret']},auth=False)
        s.tok=d['tenant_access_token']; return s.tok
    def fields(s):
        out=[];pt=None
        while True:
            d=s.req('GET',s.tbl+'/fields?page_size=100'+(f'&page_token={pt}' if pt else ''))
            out+=d.get('data',{}).get('items',[]) or []
            if not d.get('data',{}).get('has_more'): return out
            pt=d['data']['page_token']
    def ensure_fields(s):
        if s.dry: print('DRY ensure fields',[KEY]+COLS); return
        fs=s.fields(); names={f['field_name'] for f in fs}
        for f in fs:  # 旧字段名 → 新字段名（改名而不是新建，原有数据保留）
            nn=RENAME.get(f['field_name'])
            if nn and nn not in names:
                s.req('PUT',s.tbl+f"/fields/{f['field_id']}",{'field_name':nn,'type':f.get('type',1)}); names.discard(f['field_name']); names.add(nn); print('RENAMED',f['field_name'],'->',nn)
        if KEY not in names:
            prim=next((f for f in fs if f.get('is_primary')),None)
            if prim:  # 首列改名为主键、类型文本
                s.req('PUT',s.tbl+f"/fields/{prim['field_id']}",{'field_name':KEY,'type':1}); names.add(KEY)
        for c in COLS:
            if c not in names: s.req('POST',s.tbl+'/fields',{'field_name':c,'type':1})
    def existing(s,keys):
        found={}
        keys=list(keys)
        for i in range(0,len(keys),20):
            chunk=keys[i:i+20]; pt=None
            while True:
                body={'field_names':[KEY],'filter':{'conjunction':'or','conditions':[{'field_name':KEY,'operator':'is','value':[k]} for k in chunk]}}
                d=s.req('POST',s.tbl+'/records/search?page_size=500'+(f'&page_token={pt}' if pt else ''),body)
                for it in d.get('data',{}).get('items',[]) or []:
                    v=it['fields'].get(KEY)
                    if isinstance(v,list): v=''.join(x.get('text','') for x in v if isinstance(x,dict))
                    found[N(v)]=it['record_id']
                if not d.get('data',{}).get('has_more'): break
                pt=d['data']['page_token']
        return found
    def upsert(s,rows):
        s.ensure_fields()
        recs={pk(r):{KEY:pk(r),**{c:r.get(c,'') for c in COLS}} for r in rows}
        ex=s.existing(recs.keys()) if not s.dry else {}
        upd=[{'record_id':ex[k],'fields':f} for k,f in recs.items() if k in ex]
        new=[{'fields':f} for k,f in recs.items() if k not in ex]
        for i in range(0,len(upd),500): s.req('POST',s.tbl+'/records/batch_update',{'records':upd[i:i+500]})
        for i in range(0,len(new),500): s.req('POST',s.tbl+'/records/batch_create',{'records':new[i:i+500]})
        return len(upd),len(new)
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('paths',nargs='*'); ap.add_argument('--all',action='store_true')
    ap.add_argument('--csv',default='4_考学信息一览.csv'); ap.add_argument('--config',default='.feishu.json')
    ap.add_argument('--root',default='exam-monitor'); ap.add_argument('--limit',type=int); ap.add_argument('--dry-run',action='store_true')
    a=ap.parse_args()
    rows=list(csv.DictReader(open(a.csv,encoding='utf-8-sig')))
    if not a.all:
        sys.path.insert(0,os.path.dirname(os.path.abspath(__file__))); import exam_csv as ec
        root=os.path.abspath(a.root); idx=ec.load_index(root)
        want={pk(ec.row_for(root,N(p),idx)) for p in a.paths}
        rows=[r for r in rows if pk(r) in want]
    if a.limit: rows=rows[:a.limit]
    if not rows: print('没有要同步的行'); return
    cfg=json.load(open(a.config,encoding='utf-8'))
    u,n=Feishu(cfg,a.dry_run).upsert(rows)
    print(f'飞书同步完成：更新 {u} 行，新增 {n} 行')
if __name__=='__main__': main()
