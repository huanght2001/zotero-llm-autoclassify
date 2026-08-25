// apply_plan.js — 在 Zotero 内应用 zotero_classify_plan.json
// 用法: Zotero 菜单 Tools → Developer → Run JavaScript,
//       务必勾选 "Run as async function / 作为异步函数运行", 粘贴本代码, 点 Run
// 行为: 自动创建缺失分类; 条目只新增分类, 不移除原有分类
try {
  const plan = JSON.parse(await IOUtils.readUTF8("D:/hht/zotero_classify_plan.json"));
  const libID = Zotero.Libraries.userLibraryID;

  // 分类名 -> collection (不存在则创建)
  const names = [...new Set(Object.values(plan).filter(v => v))];
  const collMap = {};
  for (const name of names) {
    let c = (await Zotero.Collections.getByLibrary(libID)).find(x => x.name === name);
    if (!c) {
      c = new Zotero.Collection();
      c.libraryID = libID;
      c.name = name;
      await c.saveTx();
    }
    collMap[name] = c;
  }

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
  return `完成: 归类 ${ok} 条, 跳过 ${skip} 条, 未找到 ${miss} 条`;
} catch (e) {
  return "出错: " + e + "\n" + (e.stack || "");
}
