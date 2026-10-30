"""类别树：最多 4 级，每个节点有自己的搜索关键词。保存在 categories.json。"""
import json
import os
import uuid

MAX_DEPTH = 4


def _n(name, keywords=None, children=None):
    return {"id": uuid.uuid4().hex[:8], "name": name,
            "keywords": keywords if keywords is not None else [name],
            "children": children or []}


def default_tree():
    return [
        _n("科技", ["科技"], [
            _n("人工智能", ["人工智能", "AI"], [
                _n("大模型", ["大模型", "LLM", "ChatGPT", "Claude", "DeepSeek"]),
                _n("AI绘画", ["AI绘画", "Stable Diffusion", "Midjourney"]),
                _n("AI视频", ["AI视频生成", "Sora"]),
                _n("AI编程", ["AI编程", "Cursor"]),
            ]),
            _n("数码", ["数码"], [
                _n("手机", ["手机评测", "手机测评"]),
                _n("电脑", ["笔记本评测", "电脑装机"], [
                    _n("显卡", ["显卡评测"]),
                    _n("CPU", ["CPU评测"]),
                ]),
                _n("耳机音频", ["耳机评测"]),
                _n("平板", ["平板评测"]),
            ]),
            _n("编程开发", ["编程"], [
                _n("Python", ["Python教程"]),
                _n("前端", ["前端开发"]),
            ]),
            _n("半导体芯片", ["芯片", "半导体"]),
            _n("航天", ["航天", "火箭发射"]),
            _n("新能源汽车", ["新能源汽车", "电动车评测"]),
            _n("机器人", ["机器人", "人形机器人"]),
        ]),
    ]


class Categories:
    def __init__(self, path):
        self.path = path
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.tree = json.load(f)
        else:
            self.tree = default_tree()
            self.save()

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.tree, f, ensure_ascii=False, indent=2)

    def set_tree(self, tree):
        def check(nodes, depth):
            for n in nodes:
                if depth > MAX_DEPTH:
                    raise ValueError(f"「{n['name']}」超过了 {MAX_DEPTH} 级")
                if not n.get("name", "").strip():
                    raise ValueError("类别名称不能为空")
                n["name"] = n["name"].strip()
                n["keywords"] = [k.strip() for k in n.get("keywords", []) if k.strip()]
                n.setdefault("id", uuid.uuid4().hex[:8])
                check(n.setdefault("children", []), depth + 1)
        check(tree, 1)
        self.tree = tree
        self.save()

    def keyword_map(self, selected_ids):
        """选中节点 -> {关键词: [类别路径, ...]}。关键词为空时用类别名本身。"""
        selected = set(selected_ids)
        out = {}

        def walk(nodes, path):
            for n in nodes:
                p = path + [n["name"]]
                if n["id"] in selected:
                    for k in n["keywords"] or [n["name"]]:
                        out.setdefault(k, [])
                        if " > ".join(p) not in out[k]:
                            out[k].append(" > ".join(p))
                walk(n["children"], p)
        walk(self.tree, [])
        return out
