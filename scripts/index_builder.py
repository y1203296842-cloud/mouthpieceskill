"""scripts/index_builder.py — 自动生成知识库轻量检索索引。

扫描 knowledge/cases/*.md，生成：
  knowledge/index/case_manifest.json   每条 case 的检索用元信息（先定位用）
  knowledge/index/keyword_index.json   关键词 → case_id 列表（可选加速）
索引由脚本自动生成，**不需要用户手工维护**；ingest / rebuild 末尾自动调用。

运行：python3 scripts/index_builder.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.markdown_provider import MarkdownKnowledgeProvider, project_root


def build_index(root: str = project_root()) -> dict:
    kp = MarkdownKnowledgeProvider(root)
    cases = kp.list_cases()
    manifest = [MarkdownKnowledgeProvider._entry_for(c) for c in cases]
    # keyword_index：关键词 → case_id 列表
    kw_index: dict = {}
    for c in cases:
        for kw in c.keywords:
            if not kw:
                continue
            kw_index.setdefault(kw, [])
            if c.case_id not in kw_index[kw]:
                kw_index[kw].append(c.case_id)
    index_dir = os.path.join(root, "knowledge", "index")
    os.makedirs(index_dir, exist_ok=True)
    mp_path = os.path.join(index_dir, "case_manifest.json")
    kw_path = os.path.join(index_dir, "keyword_index.json")
    with open(mp_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    with open(kw_path, "w", encoding="utf-8") as f:
        json.dump(kw_index, f, ensure_ascii=False, indent=2)
    return {"manifest": len(manifest), "keywords": len(kw_index), "path": index_dir}


def main() -> None:
    r = build_index()
    print(f"索引已生成：{r['path']}/")
    print(f"  case_manifest.json: {r['manifest']} 条案例")
    print(f"  keyword_index.json: {r['keywords']} 个关键词")


if __name__ == "__main__":
    main()
