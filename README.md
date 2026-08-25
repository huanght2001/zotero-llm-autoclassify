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

### 1. 归类未分类条目（增量维护）

```powershell
$env:ZOTERO_LLM_API_KEY = "sk-..."        # 任意 OpenAI 兼容服务
# 可选: ZOTERO_LLM_BASE_URL / ZOTERO_LLM_MODEL

python scripts/zotero_autoclassify.py            # dry-run: 生成 zotero_classify_plan.json
python scripts/zotero_autoclassify.py --apply    # 尝试本地 API 写(若 501 走下面 JS)
```

### 2. 全库重组（新分类体系）

```powershell
$env:ZOTERO_LLM_API_KEY = "sk-..."
python scripts/zotero_reorg.py        # 全库判定 → zotero_reorg_plan.json
python scripts/make_reorg_js.py       # 生成 reorg_selfcontained.js (方案内嵌)
```

然后：记事本打开 `scripts/reorg_selfcontained.js` → 全选复制 →
Zotero *Tools → Developer → Run JavaScript* → 勾选 **Run as async function** → Run。

结果示例：`重组完成: 归类 802 条, 跳过 86 条, 未找到 2 条`

### 3. 定制你自己的分类体系

编辑 `zotero_reorg.py` 中的 `TAXONOMY` 字典即可——键是分类名（`"父/子"` 表示两级），
值是给 LLM 的收录标准描述。越具体，分类越准。LLM 拿不准时会返回 `null`（宁缺勿滥），
这些条目保持原状，适合事后人工处理。

## 文件一览

| 文件 | 作用 |
|---|---|
| `scripts/zotero_autoclassify.py` | 未分类条目 → LLM 归类（单级分类体系） |
| `scripts/zotero_reorg.py` | 全库重组（两级分类体系，主力工具） |
| `scripts/make_reorg_js.py` | 把方案 JSON 生成为自包含落库 JS |
| `scripts/apply_plan.js` | 旧版落库 JS（读文件，保留参考） |

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
