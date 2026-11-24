"""Small RAG retriever: documents -> chunks -> BM25 ranking, with strict audience separation.

A visitor index is built ONLY from documents whose audience is 'public', so member documents
are never even loaded when a visitor asks a question.
"""
import math
import re
from collections import Counter

import os

from database.init_db import CAMPUS_DB

from .campus_data import rows

STOP = set("""a an the is are was were be been am do does did to of in on at for from by with and or but if
what which who whom how when where why can could should would will shall may might i me my we our you your it its
this that these those there here about as into than then so such not no yes please tell give show let us
available many much list all any also have has get gave known""".split())
_CACHE = {}
# questions say "what time does it open", documents say "hours": map a few common words to one form
SYNONYMS = {"time": "hour", "timing": "hour", "open": "hour", "close": "hour", "closing": "hour", "schedule": "hour"}


def stem(w):
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("es"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    if len(w) > 5 and w.endswith("ed"):
        return w[:-2]
    return w


def tokenize(text):
    return [stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP]


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def chunk(body, max_words=70):
    """Split a document into passages of about max_words, keeping sentences whole."""
    out, cur, n = [], [], 0
    for sent in sentences(body):
        w = len(sent.split())
        if cur and n + w > max_words:
            out.append(" ".join(cur)); cur, n = [], 0
        cur.append(sent); n += w
    if cur:
        out.append(" ".join(cur))
    return out


class Index:
    def __init__(self, docs):
        self.chunks = []
        for d in docs:
            for text in chunk(d["body"]):
                toks = tokenize(d["title"]) + tokenize(text)
                self.chunks.append({"doc_id": d["id"], "title": d["title"], "text": text, "tf": Counter(toks), "len": len(toks)})
        n = len(self.chunks) or 1
        self.avg = sum(c["len"] for c in self.chunks) / n
        df = Counter()
        for c in self.chunks:
            df.update(c["tf"].keys())
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.max_idf = math.log(1 + (n + 0.5) / 0.5)  # weight of a word that no document contains

    def search(self, query, k=3, k1=1.5, b=0.75):
        q = list(dict.fromkeys(SYNONYMS.get(t, t) for t in tokenize(query)))
        total = sum(self.idf.get(t, self.max_idf) for t in q) or 1.0
        scored = []
        for c in self.chunks:
            s = 0.0
            for t in q:
                f = c["tf"].get(t, 0)
                if f:
                    s += self.idf.get(t, 0) * f * (k1 + 1) / (f + k1 * (1 - b + b * c["len"] / (self.avg or 1)))
            if s > 0:
                cover = sum(self.idf.get(t, 0) for t in q if t in c["tf"]) / total
                scored.append((s, cover, c))
        scored.sort(key=lambda x: -x[0])
        return [{"score": round(s, 3), "coverage": round(cv, 2), "title": c["title"], "text": c["text"]} for s, cv, c in scored[:k]]


def _index(audiences, facts=None):
    marks = ",".join("?" * len(audiences))
    ver = rows("SELECT COUNT(*) AS n, COALESCE(MAX(updated), 0) AS u, COALESCE(SUM(LENGTH(body)), 0) AS l "
               "FROM documents WHERE audience IN (%s)" % marks, tuple(audiences))[0]
    key = (tuple(sorted(audiences)), facts)
    try:
        changed = os.path.getmtime(CAMPUS_DB)  # any edit to campus.db rebuilds the fact passages
    except OSError:
        changed = 0
    stamp = (ver["n"], ver["u"], ver["l"], changed)
    hit = _CACHE.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    docs = rows("SELECT id, title, body FROM documents WHERE audience IN (%s)" % marks, tuple(audiences))
    if facts:
        from . import facts as facts_mod  # passages made from the database rows this tier may read
        docs += facts_mod.build(facts)
    idx = Index(docs)
    _CACHE[key] = (stamp, idx)
    return idx


def search(query, audiences, k=3, facts=None):
    return _index(audiences, facts).search(query, k)


def best_sentences(question, text, n=2):
    """Pick the sentences of a passage that share the most words with the question (for the no-LLM answer)."""
    q = {SYNONYMS.get(t, t) for t in tokenize(question)}
    sents = sentences(text)
    ranked = sorted(range(len(sents)), key=lambda i: -len(q & set(tokenize(sents[i]))))
    keep = sorted(i for i in ranked[:n] if q & set(tokenize(sents[i])) or i == ranked[0])
    return " ".join(sents[i] for i in keep)