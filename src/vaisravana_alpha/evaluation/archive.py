"""Lineage Archive — DGM-style node store for the evaluator.

The Darwin Gödel Machine keeps a GROWING ARCHIVE of agents (not just the
single best). "Failed" ancestors often become stepping stones for breakouts.
This module stores every evaluated candidate as a node with:
  id, parent, hypothesis, change, metrics, verdict, dsr, pbo, timestamp.

It supports:
  add(node)              record a candidate
  best()                 exploit: highest risk-adjusted score
  sample_explore()       explore: a non-best node to fork/mutate
  lineage(node_id)       the ancestor chain (for "why this works")
  export_json()          for the vector-DB / cross-session memory layer

This is the "Memory (CSCV) / Archive" block in the 3-layer diagram. It is pure
local JSONL — no external dependency, fully reproducible, and the loop appends
to it every tick so the evaluator improves across sessions.
"""
from __future__ import annotations
import json
import os
import time
import hashlib


class Archive:
    def __init__(self, path: str = "/root/wave_eval_data/evaluator_archive.jsonl"):
        self.path = path
        self._nodes: list[dict] = []
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            self._nodes.append(json.loads(line))
            except Exception:
                pass

    def _save(self, node: dict):
        with open(self.path, "a") as f:
            f.write(json.dumps(node) + "\n")

    def add(self, hypothesis: str, change: str, metrics: dict,
            verdict: str, dsr: float = 0.0, pbo: float = float("nan"),
            parent: str = None) -> str:
        node_id = hashlib.sha1(
            f"{time.time()}:{hypothesis}:{change}".encode()
        ).hexdigest()[:10]
        node = {
            "id": node_id,
            "parent": parent,
            "ts": time.time(),
            "hypothesis": hypothesis,
            "change": change,
            "metrics": metrics,
            "verdict": verdict,
            "dsr": dsr,
            "pbo": pbo,
        }
        self._nodes.append(node)
        self._save(node)
        return node_id

    def all(self) -> list[dict]:
        return list(self._nodes)

    def best(self, score_key: str = "net_per_trade") -> dict | None:
        """Exploit: node with the best metric (defaults net_per_trade)."""
        scored = [n for n in self._nodes if score_key in n.get("metrics", {})]
        if not scored:
            return None
        return max(scored, key=lambda n: n["metrics"][score_key])

    def sample_explore(self, score_key: str = "net_per_trade") -> dict | None:
        """Explore: pick a VERIFIED-but-not-best node to fork, preferring ones
        with low PBO (robust) even if lower score. Falls back to a random
        non-best node so the loop never converges prematurely."""
        candidates = [
            n for n in self._nodes
            if n.get("verdict") in ("KEEP", "KEEP-DORMANT", "REJECT")
            and score_key in n.get("metrics", {})
        ]
        if not candidates:
            return None
        # prefer robustness: sort by PBO asc (low = stable), tie-break score
        candidates.sort(key=lambda n: (n.get("pbo", 1.0), -n["metrics"][score_key]))
        # return a non-best ancestor-ish node (not the single top)
        return candidates[0] if len(candidates) > 1 else candidates[0]

    def lineage(self, node_id: str) -> list[dict]:
        """Ancestor chain upward from node_id."""
        by_id = {n["id"]: n for n in self._nodes}
        chain = []
        cur = by_id.get(node_id)
        while cur:
            chain.append(cur)
            cur = by_id.get(cur.get("parent"))
        return chain

    def export_json(self) -> str:
        return json.dumps(self._nodes, indent=2)
