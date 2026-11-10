"""Help topics: docs/help/help.index.json + Markdown files, with full-text search."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class HelpTopic:
    id: str
    category: str
    title: str
    path: str
    tags: list[str] = field(default_factory=list)


@dataclass
class SearchHit:
    topic: HelpTopic
    score: int
    snippet: str


class HelpService:
    def __init__(self, docs_dir: Path):
        self.docs_dir = Path(docs_dir)

    def topics(self) -> list[HelpTopic]:
        try:
            raw = json.loads((self.docs_dir / "help.index.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return []
        topics = []
        for item in raw if isinstance(raw, list) else []:
            try:
                topics.append(HelpTopic(id=str(item["id"]), category=str(item.get("category", "Other")),
                                        title=str(item.get("title", item["id"])), path=str(item["path"]),
                                        tags=[str(t) for t in item.get("tags", [])]))
            except (KeyError, TypeError):
                continue
        return topics

    def get(self, topic_id: str) -> HelpTopic | None:
        return next((t for t in self.topics() if t.id == topic_id), None)

    def markdown(self, topic: HelpTopic) -> str:
        base = self.docs_dir.resolve()
        path = (base / topic.path).resolve()
        if base not in path.parents or path.suffix.lower() != ".md":
            return f"*Invalid help path: {topic.path}*"
        try:
            raw = path.read_bytes()
        except OSError:
            return f"*Help file not found: {topic.path}*"
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return raw.decode("cp1252", errors="replace")  # older docs were saved by Windows editors

    def search(self, query: str, limit: int = 20) -> list[SearchHit]:
        words = [w for w in re.split(r"\s+", query.strip().casefold()) if w]
        if not words:
            return []
        hits = []
        for topic in self.topics():
            text = self.markdown(topic)
            haystack = text.casefold()
            meta = f"{topic.title} {' '.join(topic.tags)}".casefold()
            if not all(w in haystack or w in meta for w in words):
                continue
            score = sum(10 * meta.count(w) + haystack.count(w) for w in words)
            hits.append(SearchHit(topic, score, _snippet(text, words[0])))
        return sorted(hits, key=lambda h: -h.score)[:limit]


def _snippet(text: str, word: str, width: int = 140) -> str:
    plain = re.sub(r"[`*#|>\[\]]", "", text)
    plain = re.sub(r"\s+", " ", plain)
    i = plain.casefold().find(word)
    if i < 0:
        return plain[:width].strip()
    start = max(i - width // 3, 0)
    return ("..." if start else "") + plain[start:start + width].strip() + "..."
