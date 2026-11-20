"""Knowledge-base retrieval.

Starts as dependency-free BM25 over markdown files so the whole pipeline runs
locally. The `Retriever` protocol is the seam for swapping in pgvector/Qdrant
later -- and the eval suite tells you whether the swap actually helped."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = set("a an and are as at be by can do for from how i if in is it my of on or the to we what when with you your".split())


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    body: str

    def render(self) -> str:
        return f'<article id="{self.id}" title="{self.title}">\n{self.body.strip()}\n</article>'


class Retriever(Protocol):
    def search(self, query: str, k: int = 3) -> list[Article]: ...


def load_articles(kb_dir: Path) -> list[Article]:
    articles = []
    for path in sorted(kb_dir.glob("*.md")):
        text = path.read_text()
        first, _, rest = text.partition("\n")
        articles.append(Article(id=path.stem, title=first.lstrip("# ").strip(), body=rest))
    if not articles:
        raise FileNotFoundError(f"no .md articles in {kb_dir}")
    return articles


class BM25Retriever:
    def __init__(self, articles: list[Article], k1: float = 1.5, b: float = 0.75) -> None:
        self.articles = articles
        self.k1, self.b = k1, b
        # Title counted twice: titles are short and highly indicative.
        self.docs = [Counter(tokenize(f"{a.title} {a.title} {a.body}")) for a in articles]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg_len = sum(self.lengths) / len(self.lengths)
        df: Counter[str] = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, query: str, i: int) -> float:
        doc, length = self.docs[i], self.lengths[i]
        s = 0.0
        for t in tokenize(query):
            if t not in doc:
                continue
            tf = doc[t]
            s += self.idf[t] * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * length / self.avg_len))
        return s

    def search(self, query: str, k: int = 3) -> list[Article]:
        scored = sorted(((self.score(query, i), i) for i in range(len(self.docs))), reverse=True)
        return [self.articles[i] for s, i in scored[:k] if s > 0]
