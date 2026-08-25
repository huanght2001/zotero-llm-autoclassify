# zotero-llm-autoclassify

**用 LLM 给 Zotero 文献库自动分类/重组的轻量工具链** —— 纯 Python 标准库，零依赖；本地 API 读取 + LLM 语义分类 + Run JavaScript 安全落库。

## 为什么需要它

Zotero 用久了，"未分类条目"（Unfiled Items）会越堆越多；分类体系也常常是随手长出来的，主题、用途混杂。人工整理几百条既枯燥又慢。

这套工具解决两件事：

1. **增量归类**：定期把所有"不在任何分类里"的条目交给 LLM 按你的分类体系归类
2. **全库重组**：按文献内容把整个库归入一套新的两级分类体系（新旧并存，可随时回退）

## 工作原理

```
Zotero 本地 API (只读)          OpenAI 兼容 LLM API            Zotero Run JavaScript
  拉取条目标题/作者/年份/摘要  →   按自定义分类体系判定    →   自包含 JS 自动建分类并归类
        (127.0.0.1:23119)        (DeepSeek/智谱/…)            (只追加归属，永不移除)
```

三段式设计的关键取舍：

- **读**走 Zotero 本地 HTTP API（需在 设置→高级 中开启"允许其他应用通过本地 API 通信"）
- **写**不依赖任何第三方插件：Zotero 7 本地 API 不支持写操作（HTTP 501），
  因此由 Python 把分类方案直接**内嵌**生成一份自包含 JavaScript，
  在 Zotero 自带的 *Tools → Developer → Run JavaScript* 中粘贴运行（勾选"作为异步函数运行"）。
  落库逻辑只做 `addToCollection`（追加分类），**绝不会移除条目或已有归属**，天然可回退。

## 使用

### 1. 获得分类体系（二选一）

**方式 A — 让 LLM 从你的库里归纳**（推荐起步）：

```powershell
python scripts/zotero_reorg.py --survey
```

均匀抽样 250 条 → LLM 分析主题分布 → 生成分类树初稿 `scripts/taxonomy.json`（两级、带收录标准、
大主题自动拆子分类、含兜底分类）。**你只需人工审改**：改名、删掉不想要的簇、微调收录标准。

**方式 B — 手写**：复制 `scripts/taxonomy.example.json` 为 `scripts/taxonomy.json` 自行编辑。

### 2. 运行分类（可选范围）

```powershell
$env:ZOTERO_LLM_API_KEY = "sk-..."
python scripts/zotero_reorg.py                      # 整库 (默认)
python scripts/zotero_reorg.py --scope unfiled      # 只归"不在任何分类"的条目 (增量维护, 最省)
python scripts/zotero_reorg.py --scope "COLL:PAs"   # 只归某个已有分类内的条目
python scripts/zotero_reorg.py --review             # 归类后逐条确认 (y/n/a/q)
python scripts/zotero_reorg.py --multi              # 允许横跨文献归入最多2个分类
python scripts/zotero_reorg.py --subset "01-,02-"   # 只允许归入指定顶级子树 (省token、防误归)
```

归类完成后**自动生成** `scripts/reorg_selfcontained.js`（无需单独命令）。若只想从已有方案
重新生成 JS（不调 LLM、不需要 API key）：`python scripts/zotero_reorg.py --js-only`。

然后：记事本打开 `scripts/reorg_selfcontained.js` → 全选复制 →
Zotero *Tools → Developer → Run JavaScript* → 勾选 **Run as async function** → Run。

结果示例：`重组完成: 归类 802 条, 跳过 86 条, 未找到 2 条`

### 3. 选项说明

`--subset` 按顶级前缀过滤候选分类树（如 `"01-,02-"`），降低 token 成本并防止误归入不参与
本次工作的分支。`--review` 在方案生成后逐条显示「标题 → 分类」：`y` 保留 / `n` 跳过该条 /
`a` 保留剩余全部 / `q` 中止，确认结果写回方案文件。`--multi` 允许横跨两个主题的文献归入
最多 2 个分类（默认每条 1 个）。

### 4. 定制说明

分类标准越具体，LLM 判定越准（写关键词、典型方法、代表对象）。拿不准的条目 LLM 会返回
`null`（宁缺勿滥），保持原状，适合事后人工处理。

## 文件一览

| 文件 | 作用 |
|---|---|
| `scripts/zotero_reorg.py` | 一体化主工具：--survey 归纳分类树 / 分类（--scope/--subset/--multi/--review）/ 生成自包含落库 JS |
| `scripts/taxonomy.example.json` | 分类体系示例配置 |

## 安全说明

- API key 只从环境变量读取，代码与仓库中不存储任何密钥
- 落库脚本只**新增**分类归属，不删除条目、不移除已有分类、不改元数据
- 全程本地运行（LLM 只收到标题/作者/年份/摘要片段）
