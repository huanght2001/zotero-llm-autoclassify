# -*- coding: utf-8 -*-
"""
zotero_autoclassify.py — Zotero 未分类条目 LLM 自动归类（dry-run 优先）

用法:
  1. 配置环境变量(或改下方 CONFIG):
       ZOTERO_LLM_API_KEY   你的 LLM API key (DeepSeek / 智谱 / 任意 OpenAI 兼容)
       ZOTERO_LLM_BASE_URL  默认 https://api.deepseek.com/v1
       ZOTERO_LLM_MODEL     默认 deepseek-chat
  2. 确保 Zotero 桌面版已打开(本地 API 开启: 设置→高级→允许其他应用通过本地 API 通信)
  3. 生成归类方案(不写库):     python zotero_autoclassify.py
  4. 应用方案(尝试本地API写):  python zotero_autoclassify.py --apply
     若 Zotero 版本不支持本地写, 会提示改用 apply_plan.js (Zotero 内 Run JavaScript 运行)

输出: zotero_classify_plan.json
"""
import json, os, sys, time, urllib.request, urllib.error

# ----------------------------- CONFIG ---------------------------------
ZOTERO_LOCAL = "http://127.0.0.1:23119/api/users/0"
PLAN_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zotero_classify_plan.json")
LLM_KEY      = os.environ.get("ZOTERO_LLM_API_KEY", "")
LLM_BASE     = os.environ.get("ZOTERO_LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL    = os.environ.get("ZOTERO_LLM_MODEL", "deepseek-v4-flash")
BATCH        = 20          # 每次 LLM 调用处理的条目数
SKIP_TYPES   = {"note", "attachment"}   # 笔记/附件默认跳过
# ----------------------------------------------------------------------

# 分类体系: 名称 -> 判定标准 (来自 2026-05 全库人工分析, 详见 zotero_整理清单.md)
TAXONOMY = {
    "zoonoticlandscape": "土地利用变化、森林砍伐/破碎化、农业扩张与人畜共患病、溢出(spillover)、新发传染病EID、One Health、景观流行病学、宿主-病原生态学、人畜共患病原与临床研究",
    "捕食者与宿主": "宿主动物(蝙蝠/啮齿/灵长等)生态学、捕食者-猎物关系、物种群落与生物多样性本体研究(非疾病议题)",
    "PAs": "保护地/国家公园成效评估、森林保护、反事实/匹配方法评估保护成效、保护政策与管理",
    "PAShape": "保护地或景观几何形状、形态学指标、景观格局与破碎化对成效的影响、shape index/核心区-边缘",
    "cooling": "保护地/城市绿地/生态修复的降温效应、热岛、气候调节服务",
    "mrio": "多区域投入产出MRIO、贸易遥联 telecoupling、消费驱动土地利用、供应链足迹(EORA/EXIOBASE等)",
    "生态系统服务与GEP核算": "生态系统服务评估与价值化、GEP核算、自然资本(不匹配其他分类时)",
    "SDG": "可持续发展目标SDGs、生物多样性框架(如爱知目标/昆明-蒙特利尔框架)、全球环境治理目标",
    "武装冲突": "武装冲突与环境和保护、战争、政治暴力、UCPD数据",
    "入侵植物": "外来入侵物种(植物为主)、入侵生态学",
    "风电碳潜力": "风电、可再生能源碳减排潜力",
    "有趣的数据": "数据集与数据源介绍(Hansen GFC、SoilGrids、ASTER、WWF等), 标题以数据集名为核心的文献; 以及有趣的跨界生态/环境阅读, 暂无明确归属时用",
    "r": "统计与计量方法: 面板数据、因果推断(DID/合成控制/匹配)、时间序列、非参数检验、空间统计方法",
    "R包": "R 语言软件包的介绍论文(如 mgcv、terra 等 package 论文)",
    "META": "Meta 分析方法论与系统综述方法",
    "风电碳潜力X": None,  # 占位删除
}
TAXONOMY.pop("风电碳潜力X")
TAXONOMY["追新阅读"] = "领域最新动态、观点/评论/新闻类、PNAS/Science/Nat 评论等泛读文献"
TAXONOMY["PA文献共享"] = "课题组共享的保护地文献(他人标注共享)"
TAXONOMY["课题组一起读"] = "课题组共读文献"
TAXONOMY["老师发的"] = "导师推荐文献(通常难以从标题判断, 拿不准时不用)"
TAXONOMY["课程阅读材料"] = "课程阅读"
TAXONOMY["大作业"] = "课程作业相关"
TAXONOMY["My Notes"] = "仅限笔记类条目"
# 用途类分类(老师发的/课题组一起读/课程阅读材料/大作业/追新阅读)难以从标题判断,
# 除非标题明显是评论/新闻, 否则 LLM 应优先用主题分类。

SYSTEM_PROMPT = """你是文献库管理员。给定分类体系(名称:收录标准)和一批未分类文献(标题/作者/年份),
为每条选择最合适的一个分类, 或 null 表示无法判断。规则:
1. 只输出 JSON: {"<itemKey>": "<分类名或null>", ...}, 不要其他文字。
2. 主题优先于用途类分类; 拿不准用 null, 宁缺勿滥。
3. 每条只归一个分类。
分类体系:
""" + "\n".join(f"- {k}: {v}" for k, v in TAXONOMY.items())


def api(url, method="GET", body=None, token=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Zotero-API-Version", "3")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
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
        "model": LLM_MODEL,
        "temperature": 0,
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
    apply = "--apply" in sys.argv
    if not LLM_KEY and not (apply and os.path.exists(PLAN_FILE)):
        sys.exit("请先设置 ZOTERO_LLM_API_KEY 环境变量")

    if os.path.exists(PLAN_FILE) and apply:
        plan = json.load(open(PLAN_FILE, encoding="utf-8"))
    else:
        print("拉取全库条目 ...")
        items = fetch_all_items()
        unfiled = [i for i in items
                   if i["data"].get("itemType") not in SKIP_TYPES
                   and not i["data"].get("collections")]
        print(f"共 {len(items)} 条, 未分类 {len(unfiled)} 条")
        plan, total = {}, len(unfiled)
        for n in range(0, total, BATCH):
            batch = unfiled[n:n + BATCH]
            result = llm_classify(batch)
            plan.update(result)
            print(f"  已分类 {min(n + BATCH, total)}/{total}")
            time.sleep(0.5)
        plan = {k: v for k, v in plan.items() if v in TAXONOMY}  # 丢弃非法分类名
        json.dump(plan, open(PLAN_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"方案已写入 {PLAN_FILE}")

    from collections import Counter
    print("\n归类分布:", dict(Counter(v for v in plan.values() if v)))
    print("null/未定:", sum(1 for v in plan.values() if not v))

    if not apply:
        print("\n(dry-run 完成, 未写库。检查 plan 后运行 --apply)")
        return
    # 尝试本地 API 写: PATCH item, 追加 collection
    print("\n尝试通过本地 API 应用 ...")
    colls = {c["data"]["name"]: c["key"] for c in api(f"{ZOTERO_LOCAL}/collections?format=json&limit=100")}
    ok, fail = 0, 0
    items = {i["key"]: i for i in fetch_all_items()}
    for key, cname in plan.items():
        if not cname or key not in items:
            continue
        ckey = colls.get(cname)
        if not ckey:
            print(f"  [跳过] 分类不存在: {cname} (请先在 Zotero 中创建)")
            continue
        it = items[key]
        if ckey in it["data"]["collections"]:
            continue
        try:
            api(f"{ZOTERO_LOCAL}/items/{key}", method="PATCH",
                body={"collections": it["data"]["collections"] + [ckey]})
            ok += 1
        except urllib.error.HTTPError as e:
            fail += 1
            if e.code in (405, 404, 501):
                sys.exit(f"本地 API 不支持写 (HTTP {e.code})。\n请改用 apply_plan.js: "
                         "Zotero 菜单 Tools→Developer→Run JavaScript, 粘贴运行即可应用 {PLAN_FILE}")
    print(f"完成: 成功 {ok}, 失败 {fail}")


if __name__ == "__main__":
    main()
