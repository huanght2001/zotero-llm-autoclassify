# -*- coding: utf-8 -*-
"""make_reorg_js.py — 把 zotero_reorg_plan.json 生成自包含 JS (支持 父/子 两级分类)"""
import json, os

BASE = os.path.dirname(os.path.abspath(__file__))
plan = json.load(open(os.path.join(BASE, "zotero_reorg_plan.json"), encoding="utf-8"))

TEMPLATE = r'''// reorg_selfcontained.js - Zotero Run JavaScript (勾选"作为异步函数运行") 中直接运行
// 新旧并存: 自动创建新分类树(含子分类), 条目追加新分类, 不移除任何旧分类
try {
  const plan = __PLAN__;
  const libID = Zotero.Libraries.userLibraryID;

  // 1. 建 "父/子" 两级分类树
  const collMap = {}; // name -> collection
  const wanted = [...new Set(Object.values(plan).filter(v => v))].sort();
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
    for (const [key, name] of Object.entries(plan)) {
      if (!name) { skip++; continue; }
      const item = await Zotero.Items.getByLibraryAndKeyAsync(libID, key);
      if (!item || item.isNote() || item.isAttachment()) { miss++; continue; }
      const col = collMap[name];
      if (item.inCollection(col)) { skip++; continue; }
      item.addToCollection(col.id);
      await item.save();
      ok++;
    }
  });
  return '重组完成: 归类 ' + ok + ' 条, 跳过 ' + skip + ' 条, 未找到 ' + miss + ' 条';
} catch (e) {
  return '出错: ' + e + ' | ' + (e.stack || '');
}'''

js = TEMPLATE.replace("__PLAN__", json.dumps(plan, ensure_ascii=False))
open(os.path.join(BASE, "reorg_selfcontained.js"), "w", encoding="utf-8").write(js)
print("written reorg_selfcontained.js, items:", len(plan))
