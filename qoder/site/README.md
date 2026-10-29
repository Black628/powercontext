# Recall（知返）· 项目展示站

纯静态展示站，深色科技风，无任何外部 CDN / 字体 / 图片。打开 `index.html` 即可浏览。

## 文件

| 文件 | 说明 |
| --- | --- |
| `index.html` | 页面结构；架构图为内联 SVG，无图片依赖 |
| `style.css` | 全部样式，含 420 / 640 / 760 / 900 / 980px 断点 |
| `script.js` | 移动端导航、滚动高亮、命令复制（无第三方依赖） |
| `README.md` | 本说明 |

## 本地预览

直接双击 `index.html`，或在 `qoder/` 目录执行：

```bash
python -m http.server 8080 -d site
```

然后访问 <http://127.0.0.1:8080>。

## 数据口径（重要）

- 所有实测数字来自 `../docs/verification.md` 的 **5.1 / 5.2 / 5.3** 节，不得手改。
- 样例 3 为 **PASS**：`app/memory.py` 已接入 PowerContext 1.4.1（`scope` + `remember` + `entries/list`），页面展示真实写入、重读命中与 `/api/review` 证据。
- 4 个样例整体为 **4/4 PASS**，退出码 0。
- 样例 4 的手动基线尚未实测（`MANUAL_LOOKUP_SECONDS=0`）；因此页面**不出现任何提效倍数**，只写“不计算提效倍数”。
- “使用效果”一栏保持“待补充”，不预填使用人数 / 次数。补测与收集后再更新页面。

## 编码

四个文件均为 **UTF-8 无 BOM**。修改后可自检：

```bash
python -X utf8 - <<'PY'
from pathlib import Path
for p in sorted(Path('.').glob('*')):
    if p.suffix not in {'.html', '.css', '.js', '.md'}:
        continue
    raw = p.read_bytes()
    text = raw.decode('utf-8')
    bad = [c for c in text if c == '\ufffd' or 0xE000 <= ord(c) <= 0xF8FF]
    print(p.name, len(raw), text[:60] + ('…' if len(text) > 60 else ''), 'bad=', bad)
PY
```

## 发布

在 Qoder Sites 发布本目录。发布后若修改内容，需要再次点击“发布”才会更新线上版本。
