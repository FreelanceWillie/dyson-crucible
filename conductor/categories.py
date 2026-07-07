"""
categories.py - the asset category TREE and its inherited "style DNA".

Why this exists
---------------
A solo dev wants his assets to feel like they belong together. A "category" is a
node in a tree that carries shared style settings; every asset placed inside a
category inherits that node's DNA, and every sub-category refines its parent. So
`Heroes / Frost Faction / Knights` stacks three layers of style onto a knight,
and its siblings come out cohesive for free.

Nothing here is edited by hand in normal use - the dashboard's tree UI writes
`categories.yaml`. This module is the single source of truth for:
  - the tree structure (who is nested under whom)
  - each node's inheritable settings (style words, references, defaults)
  - resolving the *effective* settings for any category path (root -> leaf merge)
  - folding a category's DNA under an asset's own brief (asset overrides win)

Storage
-------
`categories.yaml` at the repo root, a FLAT map keyed by full path so it is easy
for the GUI to add/rename/move/delete nodes and easy for a human to read:

    categories:
      Heroes:        {parent: null,   style_prompt: "game hero, painterly",
                      negative: "", reference_set: "Heroes",
                      ip_adapter_weight: 0.8, note: ""}
      Heroes/Frost:  {parent: Heroes, style_prompt: "icy, frostbitten palette",
                      reference_set: "Heroes/Frost", ...}

A node's `reference_set` defaults to its own path, so style images live at
`references/<path>/` and mirror the tree.

This module is dependency-light: PyYAML + stdlib. It imports cfg lazily so it is
safe to import even before the rest of the package is wired.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise ImportError("PyYAML is required for categories. Install: pip install pyyaml") from exc


def _repo_root() -> str:
    """Repo root (parent of the conductor package). Uses cfg if available."""
    try:
        from cfg import REPO_ROOT  # type: ignore
        return REPO_ROOT
    except Exception:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cats_path() -> str:
    return os.path.join(_repo_root(), "categories.yaml")


def _references_root() -> str:
    try:
        from cfg import path as cfg_path  # type: ignore
        return cfg_path("references")
    except Exception:
        return os.path.join(_repo_root(), "references")


# Every node carries exactly these inheritable fields. Keep the shape stable so
# the resolver and the GUI can rely on them.
_NODE_FIELDS = {
    "parent": None,            # full path of the parent node, or None for a root
    "style_prompt": "",        # style words appended to descendant prompts
    "negative": "",            # default negative words merged into descendants
    "reference_set": None,     # references/<this> ; defaults to the node's own path
    "ip_adapter_weight": None,  # default IP-Adapter weight (None = inherit / fall back)
    "note": "",                # freeform style note the brain is told to respect
}


def _default_doc() -> Dict[str, Any]:
    return {"categories": {}}


def load() -> Dict[str, Any]:
    """Load the category map from disk (never fails; empty map if absent)."""
    p = _cats_path()
    if not os.path.isfile(p):
        return _default_doc()
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:
        return _default_doc()
    cats = data.get("categories") or {}
    # backfill missing fields on every node so callers are safe
    for name, node in list(cats.items()):
        if not isinstance(node, dict):
            cats[name] = dict(_NODE_FIELDS)
            continue
        for k, v in _NODE_FIELDS.items():
            node.setdefault(k, v)
    return {"categories": cats}


def save(doc: Dict[str, Any]) -> str:
    """Persist the category map (atomic-ish write)."""
    p = _cats_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=True, allow_unicode=True)
    os.replace(tmp, p)
    return p


# --- tree queries -----------------------------------------------------------

def _cats(doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return (doc or load())["categories"]


def ancestry(path: str, doc: Optional[Dict[str, Any]] = None) -> List[str]:
    """Return [root, ..., path] following parent links, root first.

    Robust against cycles / missing parents (stops if it revisits a node).
    """
    cats = _cats(doc)
    chain: List[str] = []
    seen = set()
    cur: Optional[str] = path
    while cur and cur in cats and cur not in seen:
        chain.append(cur)
        seen.add(cur)
        cur = cats[cur].get("parent")
    chain.reverse()
    return chain


def children(path: Optional[str], doc: Optional[Dict[str, Any]] = None) -> List[str]:
    """Direct children of a node (or roots when path is None)."""
    cats = _cats(doc)
    return sorted(n for n, node in cats.items() if node.get("parent") == path)


def tree(doc: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Nested structure for the GUI: [{path, name, settings, children:[...]}]."""
    doc = doc or load()

    def build(path: Optional[str]) -> List[Dict[str, Any]]:
        out = []
        for c in children(path, doc):
            node = _cats(doc)[c]
            out.append({
                "path": c,
                "name": c.split("/")[-1],
                "settings": {k: node.get(k) for k in _NODE_FIELDS},
                "children": build(c),
            })
        return out

    return build(None)


# --- inheritance resolution -------------------------------------------------

def resolve(path: str, doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Merge a node's DNA down the tree (root -> leaf).

    - style_prompt: CONCATENATED root..leaf (so parent style leads, child refines).
    - negative: concatenated (deduped-ish by simple join).
    - reference_set: the DEEPEST node that sets one wins; defaults to the leaf path.
    - ip_adapter_weight: the deepest node that sets one wins.
    - note: concatenated.
    Returns a dict with those resolved fields plus `reference_dir` (abspath).
    """
    doc = doc or load()
    cats = _cats(doc)
    styles: List[str] = []
    negs: List[str] = []
    notes: List[str] = []
    ref_set: Optional[str] = None
    weight: Optional[float] = None
    for node_path in ancestry(path, doc):
        node = cats.get(node_path, {})
        if node.get("style_prompt"):
            styles.append(str(node["style_prompt"]).strip())
        if node.get("negative"):
            negs.append(str(node["negative"]).strip())
        if node.get("note"):
            notes.append(str(node["note"]).strip())
        if node.get("reference_set"):
            ref_set = str(node["reference_set"])
        if node.get("ip_adapter_weight") is not None:
            weight = float(node["ip_adapter_weight"])
    if not ref_set:
        ref_set = path  # references mirror the tree by default
    return {
        "style_prompt": ", ".join([s for s in styles if s]),
        "negative": ", ".join([n for n in negs if n]),
        "note": " ".join([n for n in notes if n]),
        "reference_set": ref_set,
        "ip_adapter_weight": weight,
        "reference_dir": os.path.join(_references_root(), *ref_set.split("/")) if ref_set else _references_root(),
    }


def effective_brief(brief: Dict[str, Any], doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Fold the brief's category DNA UNDER the brief's own values (asset wins).

    Produces the dict actually handed to gen.generate: the category's style words
    are appended to the asset prompt, negatives merged, and reference_set /
    ip_adapter_weight fall back to the category when the asset did not set them.
    The asset's own prompt/negative/weight always take precedence where present.
    """
    eff = dict(brief)
    cat_path = brief.get("category")
    if not cat_path:
        return eff  # uncategorized asset: unchanged
    r = resolve(cat_path, doc)
    # prompt: asset prompt first (the specific subject), then shared style words
    parts = [p for p in [brief.get("prompt", "").strip(), r["style_prompt"]] if p]
    eff["prompt"] = ", ".join(parts)
    # negative: merge asset + category
    negs = [n for n in [brief.get("negative", "").strip(), r["negative"]] if n]
    eff["negative"] = ", ".join(negs)
    # reference_set: asset override wins, else category
    if not brief.get("reference_set") or brief.get("reference_set") == "default":
        if r["reference_set"]:
            eff["reference_set"] = r["reference_set"]
    # ip weight: asset value wins only if explicitly set; else category default
    if brief.get("ip_adapter_weight") is None and r["ip_adapter_weight"] is not None:
        eff["ip_adapter_weight"] = r["ip_adapter_weight"]
    eff["_category_note"] = r["note"]  # brain can read this for continuity
    return eff


# --- mutations (called by the dashboard's tree editor) ----------------------

def add(path: str, parent: Optional[str] = None, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create a category node. `path` is the full path; parent is inferred from
    it if not given. Creates the matching references/<path>/ folder."""
    doc = load()
    cats = doc["categories"]
    if parent is None and "/" in path:
        parent = path.rsplit("/", 1)[0]
    node = dict(_NODE_FIELDS)
    node["parent"] = parent
    if settings:
        for k in _NODE_FIELDS:
            if k in settings and settings[k] is not None:
                node[k] = settings[k]
    cats[path] = node
    save(doc)
    try:
        os.makedirs(os.path.join(_references_root(), *path.split("/")), exist_ok=True)
    except Exception:
        pass
    return doc


def update(path: str, settings: Dict[str, Any]) -> Dict[str, Any]:
    """Patch a node's inheritable fields."""
    doc = load()
    node = doc["categories"].get(path)
    if node is None:
        raise KeyError("no such category: " + path)
    for k in _NODE_FIELDS:
        if k in settings:
            node[k] = settings[k]
    save(doc)
    return doc


def move(path: str, new_parent: Optional[str]) -> Dict[str, Any]:
    """Re-nest a node under a new parent (children follow by parent links)."""
    doc = load()
    node = doc["categories"].get(path)
    if node is None:
        raise KeyError("no such category: " + path)
    node["parent"] = new_parent
    save(doc)
    return doc


def delete(path: str, cascade: bool = False) -> Dict[str, Any]:
    """Delete a node. With cascade, also delete descendants; otherwise re-parent
    its children up to its own parent so nothing is orphaned."""
    doc = load()
    cats = doc["categories"]
    if path not in cats:
        return doc
    parent = cats[path].get("parent")
    kids = children(path, doc)
    if cascade:
        # remove the whole subtree
        stack = [path]
        while stack:
            cur = stack.pop()
            stack.extend(children(cur, doc))
            cats.pop(cur, None)
    else:
        for k in kids:
            cats[k]["parent"] = parent
        cats.pop(path, None)
    save(doc)
    return doc
