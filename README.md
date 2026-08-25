# zotero-tools

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
python scripts/make_reorg_js.py                     # 生成 reorg_selfcontained.js (方案内嵌)
```

然后：记事本打开 `scripts/reorg_selfcontained.js` → 全选复制 →
Zotero *Tools → Developer → Run JavaScript* → 勾选 **Run as async function** → Run。

结果示例：`重组完成: 归类 802 条, 跳过 86 条, 未找到 2 条`

### 3. 定制说明

分类标准越具体，LLM 判定越准（写关键词、典型方法、代表对象）。拿不准的条目 LLM 会返回
`null`（宁缺勿滥），保持原状，适合事后人工处理。

## 文件一览

| 文件 | 作用 |
|---|---|
| `scripts/zotero_reorg.py` | 全库/增量 LLM 分类（两级分类体系） |
| `scripts/make_reorg_js.py` | 把方案 JSON 生成为自包含落库 JS |

> 分类方案 JSON 与个人文献清单属于私人数据，已通过 `.gitignore` 排除在本仓库之外。

## 已知坑（亲测）

| 坑 | 解法 |
|---|---|
| Windows `python` 运行无输出直接退出 | 商店占位程序，用完整路径的真 Python |
| 本地 API 写返回 HTTP 501 | Zotero 7 本地 API 只读，落库走 Run JavaScript |
| Run JavaScript 返回 `undefined` | 未勾选 "Run as async function" |
| `NS_ERROR_FILE_UNRECOGNIZED_PATH` | Run JS 中读文件不可靠 → 改用方案内嵌的自包含 JS |
| 生成的 JS 报 `unescaped line break` | Python 生成时用原始字符串，防止 `\n` 被转义 |

## 安全说明

- API key 只从环境变量读取，代码与仓库中不存储任何密钥
- 落库脚本只**新增**分类归属，不删除条目、不移除已有分类、不改元数据
- 全程本地运行（LLM 只收到标题/作者/年份/摘要片段）
