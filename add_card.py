#!/usr/bin/env python3
"""Add or update a card in config.json.

Usage:
  python add_card.py OP01-120 --name "Shanks" \
      --snkrdunk URL --yuyu-tei URL     (Japanese versions)
      --ebay URL --shiny URL            (English versions)
Any subset of sources is fine; re-running for the same ID merges/updates.
"""
import argparse
import json
from pathlib import Path

CONFIG = Path(__file__).parent / "config.json"

p = argparse.ArgumentParser()
p.add_argument("id")
p.add_argument("--name")
for s in ("snkrdunk", "yuyu-tei", "ebay", "shiny"):
    p.add_argument(f"--{s}")
a = p.parse_args()

cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
card = next((c for c in cfg["cards"] if c["id"] == a.id), None)
if card is None:
    card = {"id": a.id, "sources": {}}
    cfg["cards"].append(card)
if a.name:
    card["name"] = a.name
for s in ("snkrdunk", "yuyu-tei", "ebay", "shiny"):
    v = getattr(a, s.replace("-", "_"))
    if v:
        card["sources"][s] = v
# drop the placeholder example once real cards exist
cfg["cards"] = [c for c in cfg["cards"]
                if not any(str(u).startswith("PASTE") for u in c["sources"].values())
                or c is card]
CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Saved {a.id}. Now tracking {len(cfg['cards'])} card(s). Commit & push config.json.")

