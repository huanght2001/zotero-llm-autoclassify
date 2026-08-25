# -*- coding: utf-8 -*-
"""
zotero_reorg.py — Zotero 文献库 LLM 分类/重组 (只追加新分类, 不动旧分类)

用法:
  python zotero_reorg.py --survey          # 模式1: 抽样归纳分类树初稿 → taxonomy.json (供人工审改)
  python zotero_reorg.py                   # 模式2: 按 taxonomy.json 归类 → zotero_reorg_plan.json
  python zotero_reorg.py --scope unfiled   # 模式2 + 范围限定 (默认 all)

范围 (--scope, 模式2可用):
  all       整库所有条目 (默认)
  unfiled   只处理不在任何分类中的条目 (增量维护, 最省钱)
  COLL:名称 只处理指定分类内的条目, 如 --scope "COLL:PAs"

前置:
  1. Zotero 已打开, 且 设置→高级 中允许本地 API 通信
  2. $env:ZOTERO_LLM_API_KEY = "sk-..."  (任意 OpenAI 兼容接口, 可选 BASE_URL/MODEL)
  3. 模式2 需要 taxonomy.json (可由 --survey 生成, 或复制 taxonomy.example.json 手写)

落库: 本地 API 只读 → python make_reorg_js.py 生成自包含 JS →
      Zotero Tools→Developer→Run JavaScript (勾选"作为异步函数运行") 执行。
"""
import json, os, sys, time, urllib.request

ZOTERO_LOCAL = "http://127.0.0.1:23119/api/users/0"
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
PLAN_FILE    = os.path.join(BASE_DIR, "zotero_reorg_plan.json")
TAXONOMY_FILE = os.path.join(BASE_DIR, "taxonomy.json")
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

def classify(scope):
    if not os.path.exists(TAXONOMY_FILE):
        sys.exit("未找到 taxonomy.json — 先运行 --survey 生成初稿, 或复制 taxonomy.example.json 手写")
    taxonomy = json.load(open(TAXONOMY_FILE, encoding="utf-8"))

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
4. 拿不准用 null, 宁缺勿滥。每条只归一个分类。
分类体系:
""" + "\n".join(f"- {k}: {v}" for k, v in taxonomy.items())

    plan = {}
    for n in range(0, len(targets), BATCH):
        batch = [brief(i) for i in targets[n:n + BATCH]]
        plan.update(llm([{"role": "system", "content": system},
                         {"role": "user", "content": json.dumps(batch, ensure_ascii=False)}]))
        print(f"  已分类 {min(n + BATCH, len(targets))}/{len(targets)}")
        time.sleep(0.5)
    plan = {k: v for k, v in plan.items() if not v or v in taxonomy}
    json.dump(plan, open(PLAN_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    from collections import Counter
    dist = Counter(v for v in plan.values() if v)
    print(f"\n方案已写入 {PLAN_FILE}")
    print(f"归类分布 (共 {sum(dist.values())} 条, null {sum(1 for v in plan.values() if not v)} 条):")
    for k, c in sorted(dist.items()):
        print(f"  {k}: {c}")
    print("\n→ 下一步: python make_reorg_js.py 生成自包含 JS, 在 Zotero Run JavaScript 中执行")


def main():
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
    classify(scope)


if __name__ == "__main__":
    main()
