# 専攻考学信息库（网页）

把 `4_考学信息一览.csv`（表4）和 `exam-monitor/` 下各専攻的 md 按「编号」联动：
表格可搜索、筛选、排序，点任意一行在右侧打开该専攻的 md 详情和表4字段。

## 使用

```bash
python3 web/build.py                 # 表4或md更新后重新运行
python3 -m http.server -d web 8000   # 然后打开 http://localhost:8000
```

`build.py` 生成 `web/data/`（不提交到 git）：
- `rows.json`：表4每一行 + 对应 md 路径；只有 md、不在表4里的専攻也会列出（标「仅md」）
- `md/{大学编号}.json`：该大学所有 md 正文，点开时按需加载

直接双击打开 `index.html` 时浏览器不允许读取数据，需要用上面的本地服务器。
网址后加 `#编号`（如 `#001A-01`）可直接打开某个専攻。
