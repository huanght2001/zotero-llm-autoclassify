# -*- coding: utf-8 -*-
"""
zotero_reorg.py — Zotero 文献库 LLM 分类/重组 一体化工具 (只追加新分类, 不动旧分类)

用法:
  python zotero_reorg.py --survey          # 模式1: 抽样归纳分类树初稿 → taxonomy.json (供人工审改)
  python zotero_reorg.py                   # 模式2: 按 taxonomy.json 归类 → zotero_reorg_plan.json
                                           #        归类完自动生成 reorg_selfcontained.js
  python zotero_reorg.py --js-only         # 只从已有 plan 重新生成 JS (不调 LLM, 不需要 API key)

选项 (模式2):
  --scope all|unfiled|COLL:名称   处理范围 (默认 all; unfiled=增量维护最省; COLL:PAs=指定分类内)
  --subset "01-,02-"              候选分类子集, 按顶级前缀过滤 (省 token、防误归)
  --multi                         允许横跨文献归入最多 2 个分类
  --review                        归类后逐条确认 (y=保留 n=跳过 a=保留剩余全部 q=中止)

前置:
  1. Zotero 已打开, 且 设置→高级 中允许本地 API 通信
  2. $env:ZOTERO_LLM_API_KEY = "sk-..."  (任意 OpenAI 兼容接口, 可选 BASE_URL/MODEL)
  3. 模式2 需要 taxonomy.json (可由 --survey 生成, 或复制 taxonomy.example.json 手写)

落库: 本地 API 只读 → 归类完自动生成 reorg_selfcontained.js →
      记事本全选复制 → Zotero Tools→Developer→Run JavaScript (勾选"作为异步函数运行") 执行。
"""
import json, os, sys, time, urllib.request

ZOTERO_LOCAL = "http://127.0.0.1:23119/api/users/0"
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
PLAN_FILE    = os.path.join(BASE_DIR, "zotero_reorg_plan.json")
TAXONOMY_FILE = os.path.join(BASE_DIR, "taxonomy.json")
JS_FILE      = os.path.join(BASE_DIR, "reorg_selfcontained.js")
LLM_KEY      = os.environ.get("ZOTERO_LLM_API_KEY", "")
LLM_BASE     = os.environ.get("ZOTERO_LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL    = os.environ.get("ZOTERO_LLM_MODEL", "deepseek-v4-flash")
BATCH        = 20
SURVEY_N     = 250        # --survey 抽样条数
SKIP_TYPES   = {"note", "attachment"}


def api(url, method="GET", body=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Zotero-API-Version", "3")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def llm(messages):
    payload = {
        "model": LLM_MODEL, "temperature": 0, "messages": messages,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(f"{LLM_BASE}/chat/completions", method="POST")
    req.add_header("Authorization", f"Bearer {LLM_KEY}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, json.dumps(payload).encode("utf-8"), timeout=180) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return json.loads(resp["choices"][0]["message"]["content"])


def fetch_all_items():
    items, start = [], 0
    while True:
        page = api(f"{ZOTERO_LOCAL}/items?format=json&limit=100&start={start}&itemType=-attachment")
        if not page:
            break
        items.extend(page)
        start += 100
        if len(page) < 100:
            break
    return items


def brief(i):
    return {"key": i["key"], "t": i["data"].get("title", ""),
            "a": (i["data"].get("creators") or [{}])[0].get("lastName", ""),
            "y": i["data"].get("date", "")[:4],
            "ab": (i["data"].get("abstractNote", "") or "")[:500]}


# ------------------------- 模式1: 归纳分类树 -------------------------

def survey():
    print(f"拉取全库并均匀抽样 {SURVEY_N} 条 ...")
    items = [i for i in fetch_all_items() if i["data"].get("itemType") not in SKIP_TYPES]
    if len(items) > SURVEY_N:
        step = len(items) / SURVEY_N
        items = [items[int(n * step)] for n in range(SURVEY_N)]
    sample = [brief(i) for i in items]

    taxonomy = llm([
        {"role": "system", "content": """你是文献信息架构师。给定一批文献样本(标题/作者/年份/摘要),
分析这个文献库的主题分布, 归纳出一棵两级分类树。规则:
1. 只输出 JSON: {"分类名或父/子名": "收录标准(关键词/方法/对象, 越具体越好)", ...}
2. 分类树从这批文献实际分布中归纳: 大主题设父分类, 超过样本15%的主题应拆出子分类
3. 顶级分类建议 4-8 个, 编号前缀(如 01- 02-)便于排序; 每个父分类的子分类 0-4 个
4. 必须包含一个兜底分类收容方法/数据/评论类文献 (如 "0N-方法与工具", "0N-泛读与动态")
5. 不强行凑数: 小而散的主题归入兜底, 不要为几个条目单独设分类"""},
        {"role": "user", "content": json.dumps(sample, ensure_ascii=False)},
    ])
    json.dump(taxonomy, open(TAXONOMY_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n分类树初稿已写入 {TAXONOMY_FILE} ({len(taxonomy)} 个分类):")
    for k in taxonomy:
        print(f"  {k}")
    print("\n→ 请人工审改 (改名/删除/调标准) 后, 运行不带参数的 zotero_reorg.py 进行归类")


# ------------------------- 模式2: 归类 -------------------------

def classify(scope, subset=None, multi=False, review=False):
    if not os.path.exists(TAXONOMY_FILE):
        sys.exit("未找到 taxonomy.json — 先运行 --survey 生成初稿, 或复制 taxonomy.example.json 手写")
    taxonomy = json.load(open(TAXONOMY_FILE, encoding="utf-8"))

    # 候选子集: --subset "01-,02-" 只允许归入这些父树
    if subset:
        keys = [k.strip() for k in subset.split(",") if k.strip()]
        allowed = {}
        for k, v in taxonomy.items():
            top = k.split("/")[0].strip()
            if any(top == s.split("/")[0].strip() for s in keys):
                allowed[k] = v
        if not allowed:
            sys.exit(f"--subset 未匹配到任何分类 (可用顶级: {', '.join(sorted({k.split('/')[0] for k in taxonomy}))})")
        print(f"候选分类子集: {len(allowed)}/{len(taxonomy)} 个 (仅这些可被选)")
        taxonomy = allowed

    print("拉取全库条目 ...")
    items = fetch_all_items()
    targets = [i for i in items if i["data"].get("itemType") not in SKIP_TYPES]

    if scope == "unfiled":
        targets = [i for i in targets if not i["data"].get("collections")]
        print(f"范围: 未分类条目")
    elif scope.startswith("COLL:"):
        name = scope[5:]
        colls = api(f"{ZOTERO_LOCAL}/collections?format=json&limit=100")
        match = [c for c in colls if c["data"]["name"] == name]
        if not match:
            sys.exit(f"未找到分类: {name} (现有: {', '.join(c['data']['name'] for c in colls)})")
        ckey = match[0]["key"]
        targets = [i for i in targets if ckey in i["data"].get("collections", [])]
        print(f"范围: 分类「{name}」内条目")
    else:
        print("范围: 整库")
    print(f"待归类 {len(targets)} 条 (笔记/附件跳过)")

    system = """你是文献库管理员。给定两级分类体系(名称:收录标准)和一批文献(标题/作者/年份/摘要),
为每条选择最合适的一个分类(可为"父"或"父/子"), 或 null。规则:
1. 只输出 JSON: {"<itemKey>": "<分类名或null>", ...}, 不要其他文字。
2. 按文献的学术内容判断, 不考虑其原有分类或来历。
3. 有更具体的子分类时优先选子分类。
4. 拿不准用 null, 宁缺勿滥。
5. MULTI 每条默认只归一个分类; 仅当文献确实横跨两个分类时, 输出 ["分类1","分类2"] 数组 (最多2个)。
分类体系:
""" + "\n".join(f"- {k}: {v}" for k, v in taxonomy.items())
    if not multi:
        system = system.replace(
            "5. MULTI 每条默认只归一个分类; 仅当文献确实横跨两个分类时, 输出 [\"分类1\",\"分类2\"] 数组 (最多2个)。",
            "5. 每条只归一个分类, 输出单个字符串。")

    plan = {}
    for n in range(0, len(targets), BATCH):
        batch = [brief(i) for i in targets[n:n + BATCH]]
        plan.update(llm([{"role": "system", "content": system},
                         {"role": "user", "content": json.dumps(batch, ensure_ascii=False)}]))
        print(f"  已分类 {min(n + BATCH, len(targets))}/{len(targets)}")
        time.sleep(0.5)
    # 归一化: 允许值为 字符串/null/列表(多分类); 非法分类名丢弃
    def norm(v):
        if v is None:
            return None
        vs = v if isinstance(v, list) else [v]
        vs = [x for x in vs if x in taxonomy]
        return vs or None
    plan = {k: norm(v) for k, v in plan.items()}
    json.dump(plan, open(PLAN_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    from collections import Counter
    flat = [c for v in plan.values() if v for c in (v if isinstance(v, list) else [v])]
    dist = Counter(flat)
    print(f"\n方案已写入 {PLAN_FILE}")
    print(f"归类分布 (共 {sum(1 for v in plan.values() if v)} 条, 其中多分类 {sum(1 for v in plan.values() if isinstance(v, list))} 条, null {sum(1 for v in plan.values() if not v)} 条):")
    for k, c in sorted(dist.items()):
        print(f"  {k}: {c}")

    # 确认环节: 逐条显示 标题→分类, 可跳过/中止, 剩余的写回方案
    if review:
        titles = {i["key"]: (i["data"].get("title", "") or "(无标题)")[:70] for i in targets}
        print("\n--- 确认环节 (y=保留 / n=跳过该条 / a=保留剩余全部 / q=中止保留已确认) ---")
        kept = {}
        for n, (key, v) in enumerate(plan.items()):
            if not v:
                continue
            label = v if isinstance(v, str) else " + ".join(v)
            ans = input(f"[{n+1}/{len(plan)}] {titles.get(key, key)} → {label} (y/n/a/q): ").strip().lower()
            if ans == "q":
                print("(中止: 仅保留此前已确认的条目)")
                break
            if ans == "a":
                kept.update({k: vv for k, vv in plan.items() if vv})
                print("(保留剩余全部)")
                break
            if ans != "n":
                kept[key] = v
        plan = kept
        json.dump(plan, open(PLAN_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"确认后方案: {len(plan)} 条已写回 {PLAN_FILE}")

    make_js(plan)


# ------------------------- 落库 JS 生成 -------------------------

JS_TEMPLATE = r'''// reorg_selfcontained.js - Zotero Run JavaScript (勾选"作为异步函数运行") 中直接运行
// 新旧并存: 自动创建新分类树(含子分类), 条目追加新分类, 不移除任何旧分类
try {
  const plan = __PLAN__;
  const libID = Zotero.Libraries.userLibraryID;

  // 1. 建 "父/子" 两级分类树 (值可为 字符串 或 [字符串数组] 多分类)
  const collMap = {}; // name -> collection
  const flat = Object.values(plan).flat().filter(v => v);
  const wanted = [...new Set(flat)].sort();
  for (const full of wanted) {
    const parts = full.split('/');
    let parent = null;
    for (const name of parts) {
      const siblings = await Zotero.Collections.getByLibrary(libID);
      let c = siblings.find(x => x.name === name &&
        ((parent === null && !x.parentID) || (parent !== null && x.parentID === parent.id)));
      if (!c) {
        c = new Zotero.Collection();
        c.libraryID = libID;
        c.name = name;
        if (parent) c.parentID = parent.id;
        await c.saveTx();
      }
      parent = c;
    }
    collMap[full] = parent;
  }

  // 2. 条目归类 (只追加)
  let ok = 0, miss = 0, skip = 0;
  await Zotero.DB.executeTransaction(async function () {
    for (const [key, val] of Object.entries(plan)) {
      if (!val) { skip++; continue; }
      const names = Array.isArray(val) ? val : [val];
      const item = await Zotero.Items.getByLibraryAndKeyAsync(libID, key);
      if (!item || item.isNote() || item.isAttachment()) { miss++; continue; }
      let changed = false;
      for (const name of names) {
        const col = collMap[name];
        if (col && !item.inCollection(col)) {
          item.addToCollection(col.id);
          changed = true;
        }
      }
      if (changed) { await item.save(); ok++; } else { skip++; }
    }
  });
  return '重组完成: 归类 ' + ok + ' 条, 跳过 ' + skip + ' 条, 未找到 ' + miss + ' 条';
} catch (e) {
  return '出错: ' + e + ' | ' + (e.stack || '');
}'''


def make_js(plan):
    js = JS_TEMPLATE.replace("__PLAN__", json.dumps(plan, ensure_ascii=False))
    open(JS_FILE, "w", encoding="utf-8").write(js)
    print(f"\n✅ 自包含落库脚本已生成: {JS_FILE} ({len(plan)} 条)")
    print("→ 记事本打开全选复制, 在 Zotero Tools→Developer→Run JavaScript (勾选\"作为异步函数运行\") 中运行")


# ------------------------- 入口 -------------------------

def main():
    if "--js-only" in sys.argv:
        if not os.path.exists(PLAN_FILE):
            sys.exit(f"未找到 {PLAN_FILE} — 先运行归类生成方案")
        make_js(json.load(open(PLAN_FILE, encoding="utf-8")))
        return
    if not LLM_KEY:
        sys.exit("请先设置 ZOTERO_LLM_API_KEY 环境变量")
    if "--survey" in sys.argv:
        survey()
        return
    scope = "all"
    if "--scope" in sys.argv:
        scope = sys.argv[sys.argv.index("--scope") + 1]
    if scope not in ("all", "unfiled") and not scope.startswith("COLL:"):
        sys.exit(f"未知范围: {scope} (可选 all / unfiled / COLL:分类名)")
    subset = None
    if "--subset" in sys.argv:
        subset = sys.argv[sys.argv.index("--subset") + 1]
    multi = "--multi" in sys.argv
    review = "--review" in sys.argv
    classify(scope, subset, multi, review)


if __name__ == "__main__":
    main()
