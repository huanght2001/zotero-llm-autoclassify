# -*- coding: utf-8 -*-
"""
zotero_reorg.py — Zotero 全库按工作内容重组 (新旧并存: 只新增新分类, 不动旧分类)

用法:
  $env:ZOTERO_LLM_API_KEY = "sk-..."
  python zotero_reorg.py            # 生成方案 zotero_reorg_plan.json (dry-run)
  python zotero_reorg.py --apply    # 尝试本地API写(大概率501, 用自包含JS替代)

新结构两级: "父分类/子分类" 形式; 条目保留所有旧分类归属, 仅追加新分类。
"""
import json, os, sys, time, urllib.request

ZOTERO_LOCAL = "http://127.0.0.1:23119/api/users/0"
PLAN_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zotero_reorg_plan.json")
LLM_KEY      = os.environ.get("ZOTERO_LLM_API_KEY", "")
LLM_BASE     = os.environ.get("ZOTERO_LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL    = os.environ.get("ZOTERO_LLM_MODEL", "deepseek-v4-flash")
BATCH        = 20
SKIP_TYPES   = {"note", "attachment"}

# 新分类体系: "父/子" 或 "父" (顶级)
TAXONOMY = {
    "01-保护区成效": "保护地/国家公园设立与管理成效、森林损失遏制、反事实/匹配评估、保护政策、OECM、保护地网络",
    "01-保护区成效/几何形状与景观格局": "保护地或景观几何形状、形态学指数、破碎化、核心区-边缘、shape index、景观格局指数",
    "01-保护区成效/降温效应": "保护地/绿地/生态修复的降温效应、热岛缓解、气候调节",
    "02-人畜共患病": "土地利用变化与传染病、溢出spillover、新发传染病EID、One Health、景观流行病学、人畜共患病原与临床",
    "02-人畜共患病/宿主与捕食者生态": "宿主动物(蝙蝠/啮齿/灵长)生态、捕食者-猎物、群落生态(非疾病本体)",
    "03-贸易与土地利用": "MRIO投入产出、telecoupling贸易遥联、消费驱动土地利用、供应链足迹、远程耦合",
    "04-方法与工具": "通用统计/计量/因果推断/空间分析/机器学习方法论 (无明显主题应用时)",
    "04-方法与工具/R包": "R 语言 package 论文 (mgcv/terra/marginaleffects 等)",
    "04-方法与工具/Meta分析与系统综述": "Meta 分析方法、系统综述方法学",
    "05-数据集": "以介绍数据集/数据源为主的文献 (Hansen GFC、SoilGrids、ASTER、UCPD、WDPA 等)",
    "06-议题专题/武装冲突": "武装冲突与环境保护、战争、政治暴力",
    "06-议题专题/入侵物种": "外来入侵物种、入侵生态学",
    "06-议题专题/可再生能源": "风电、可再生能源碳减排",
    "06-议题专题/SDG与全球治理": "SDGs、生物多样性框架(爱知/昆明-蒙特利尔)、全球环境治理",
    "06-议题专题/生态系统服务与GEP": "生态系统服务评估、GEP核算、自然资本",
    "07-泛读与动态": "观点/评论/新闻/领域动态、Science/Nat/PNAS 评论、明显泛读性质且无具体主题归属",
}

SYSTEM_PROMPT = """你是文献库管理员。给定两级分类体系(名称:收录标准)和一批文献(标题/作者/年份/摘要),
为每条选择最合适的一个分类(可为"父"或"父/子"), 或 null。规则:
1. 只输出 JSON: {"<itemKey>": "<分类名或null>", ...}, 不要其他文字。
2. 按文献的学术内容判断, 不考虑其原有分类或来历。
3. 有更具体的子分类时优先选子分类; 数据集论文若主题明确(如武装冲突用UCPD), 优先主题而非数据集。
4. 拿不准用 null, 宁缺勿滥。每条只归一个分类。
分类体系:
""" + "\n".join(f"- {k}: {v}" for k, v in TAXONOMY.items())


def api(url, method="GET", body=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Zotero-API-Version", "3")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


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


def llm_classify(batch):
    payload = {
        "model": LLM_MODEL, "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(
                [{"key": i["key"], "t": i["data"].get("title", ""),
                  "a": (i["data"].get("creators") or [{}])[0].get("lastName", ""),
                  "y": i["data"].get("date", "")[:4],
                  "ab": (i["data"].get("abstractNote", "") or "")[:500]}
                 for i in batch], ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(f"{LLM_BASE}/chat/completions", method="POST")
    req.add_header("Authorization", f"Bearer {LLM_KEY}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, json.dumps(payload).encode("utf-8"), timeout=180) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return json.loads(resp["choices"][0]["message"]["content"])


def main():
    apply_mode = "--apply" in sys.argv
    if os.path.exists(PLAN_FILE):
        plan = json.load(open(PLAN_FILE, encoding="utf-8"))
        print(f"使用已有方案 {PLAN_FILE} ({len(plan)} 条; 删除该文件可重新生成)")
    else:
        if not LLM_KEY:
            sys.exit("请先设置 ZOTERO_LLM_API_KEY")
        print("拉取全库条目 ...")
        items = fetch_all_items()
        targets = [i for i in items if i["data"].get("itemType") not in SKIP_TYPES]
        print(f"共 {len(items)} 条, 待归类 {len(targets)} 条 (笔记/附件跳过)")
        plan = {}
        for n in range(0, len(targets), BATCH):
            batch = targets[n:n + BATCH]
            plan.update(llm_classify(batch))
            print(f"  已分类 {min(n + BATCH, len(targets))}/{len(targets)}")
            time.sleep(0.5)
        plan = {k: v for k, v in plan.items() if not v or v in TAXONOMY}
        json.dump(plan, open(PLAN_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"方案已写入 {PLAN_FILE}")

    from collections import Counter
    dist = Counter(v for v in plan.values() if v)
    print(f"\n归类分布 (共 {sum(dist.values())} 条, null {sum(1 for v in plan.values() if not v)} 条):")
    for k, c in sorted(dist.items()):
        print(f"  {k}: {c}")

    if not apply_mode:
        print("\n(dry-run 完成。检查方案后运行 --apply, 或用自包含 JS 落库)")
        return
    print("\n尝试本地 API 应用 (若 501 请生成自包含 JS) ...")
    raise SystemExit("本地 API 只读, 请运行 make_reorg_js.py 生成自包含脚本后在 Zotero Run JavaScript 中执行")


if __name__ == "__main__":
    main()
