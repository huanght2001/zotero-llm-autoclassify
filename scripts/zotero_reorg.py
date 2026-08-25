# -*- coding: utf-8 -*-
"""
zotero_reorg.py — Zotero 全库按工作内容重组 (新旧并存: 只新增新分类, 不动旧分类)

用法:
  1. 复制 taxonomy.example.json 为 taxonomy.json, 改成你自己的分类体系
  2. $env:ZOTERO_LLM_API_KEY = "sk-..."
  python zotero_reorg.py            # 生成方案 zotero_reorg_plan.json (dry-run)
  python zotero_reorg.py --apply    # 尝试本地API写(大概率501, 用自包含JS替代)

新结构两级: "父分类/子分类" 形式; 条目保留所有旧分类归属, 仅追加新分类。
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
SKIP_TYPES   = {"note", "attachment"}

# 分类体系: 从 taxonomy.json 加载 (格式见 taxonomy.example.json); "父/子" 为子分类
if not os.path.exists(TAXONOMY_FILE):
    sys.exit("未找到 taxonomy.json — 请复制 taxonomy.example.json 为 taxonomy.json 并改成你的分类体系")
TAXONOMY = json.load(open(TAXONOMY_FILE, encoding="utf-8"))

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
