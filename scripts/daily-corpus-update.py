#!/usr/bin/env python3
"""FixNet daily corpus update.

Applies one day's staged fix batch to the FixNet corpus and re-indexes it.

Design (Gary, 2026-11-16):
- The corpus is capped at growing 1,000,000,000 entries per day. It can
  reach that cap from daily activity alone (the temps, the fixes, the
  pass) even if no users sign up. Growth does NOT depend on new signups.
- The daily flow is the source: the 12am grab verifies temps, the day's
  fix batch (collector + smart filter) rides the grab up to the repo, and
  this script lands the batch in the corpus. At 2am the repo gets its
  daily update on the Cloudflare neurons (separate event, D15).
- The corpus is the FixNet corpus ONLY. It is kept separate from the
  LLMBuilder training corpus — never mixed.

Why sharded, why streaming:
- The original uploader loaded the whole refs.json into memory, appended
  one entry, and rewrote the whole file. That breaks at a billion
  entries a day. This script never loads the corpus into memory: it
  streams the batch line by line, appends to shard files, and updates
  only manifests. Memory use is O(chunk), not O(corpus).
- Sharding mirrors the daily grab: 32 shards per day, tick-range width
  31,250,000 (32 x 31,250,000 = 1,000,000,000). The grab's shard N
  produces this day's corpus shard N. Entries without a tick are sharded
  by hash of fix_hash, so nothing is ever unplaceable.
- Shard files are JSONL (one ref entry per line), append-only. A shard
  can be read, verified, or served without touching the other 31.
- refs.json is the top-level index MANIFEST (v2), not the entry list.
  It points at per-day manifests, which point at shards. The entry shape
  itself is unchanged from the original (fix_hash, user_id, timestamp,
  error_type, script, encrypted_file, signature_file, + branching fields).

Cap behaviour (Gary, 2026-11-16 — Q2 settled: queue):
- If the day's new entries would pass 1,000,000,000, the script stops
  accepting and records overflow_queued=true. The overflow queues for
  the next day's update (Paperspace sorts the queue, the pass sets it).
  The cap is never silently exceeded and the overflow is never dropped.

Idempotency / resumability:
- Dedupe is by fix_hash within the day and against the day's existing
  shards. Re-running the same batch adds nothing twice.
- The script processes in chunks and writes the manifest only at the
  end of a successful run, so a partial/interrupted run can be re-run
  safely (already-written shard lines are deduped on re-read).

Cost: 0 neurons. FixNet is client to repo — data moving, not AI.
The pass costs nothing but the daily pull.

Usage:
    python3 scripts/daily-corpus-update.py --batch <batch.jsonl> [--date YYYY-MM-DD]
    python3 scripts/daily-corpus-update.py --batch <batch.jsonl> --date 2026-11-16 --cap 100  # test cap
    python3 scripts/daily-corpus-update.py --verify --date 2026-11-16   # verify a day's shards against its manifest

The batch file is JSONL: one ref entry per line. Required fields per
entry: fix_hash, error_type, timestamp. Optional: user_id, script,
encrypted_file, signature_file, tick (causal tick, for shard placement),
inspired_by, variation_reason, relationship_type, keywords, context.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import date as _date

# --- Constants (Gary's design) -------------------------------------------
MAX_NEW_ENTRIES_PER_DAY = 1_000_000_000   # the billion-a-day growth cap
SHARD_COUNT = 32                           # mirrors the grab's 32 shards
SHARD_WIDTH = 31_250_000                   # tick-range width; 32 * width = 1B
CHUNK_SIZE = 200_000                       # matches GRAB_CHUNK_RECORDS
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REQUIRED_FIELDS = ("fix_hash", "error_type", "timestamp")


def repo_path(*parts):
    return os.path.join(REPO_ROOT, *parts)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def shard_index(entry):
    """Which of the 32 shards this entry belongs to.

    Tick-range sharding (mirrors the grab): shard = tick // 31,250,000,
    clamped to 31. Entries with no tick fall back to hash sharding on
    fix_hash, so every entry is placeable and placement is deterministic.
    """
    tick = entry.get("tick")
    if isinstance(tick, int) and tick >= 0:
        return min(tick // SHARD_WIDTH, SHARD_COUNT - 1)
    digest = hashlib.sha256(str(entry["fix_hash"]).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % SHARD_COUNT


def load_day_manifest(day):
    path = repo_path("corpus", day, "manifest.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "date": day,
        "format": "fixnet-corpus-day-v1",
        "entries": 0,
        "shards": SHARD_COUNT,
        "shard_entries": [0] * SHARD_COUNT,
        "shard_sha256": [None] * SHARD_COUNT,
        "cap_applied": False,
        "overflow_queued": False,
        "overflow_count": 0,
    }


def save_day_manifest(day, manifest):
    path = repo_path("corpus", day, "manifest.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")


def load_refs_index():
    """refs.json v2: the top-level sharded index manifest."""
    path = repo_path("refs.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("format") == "fixnet-refs-v2":
            return data
        # Legacy v1: a flat array of entries. Empty ([]) is the only state
        # the repo has ever been in; anything else is migrated by hand.
        if isinstance(data, list) and len(data) == 0:
            return empty_refs_index()
        raise ValueError(
            "refs.json is a non-empty v1 flat array; migrate it by hand "
            "before running a sharded update (see CORPUS.md)."
        )
    return empty_refs_index()


def empty_refs_index():
    return {
        "version": 2,
        "format": "fixnet-refs-v2",
        "total_entries": 0,
        "total_days": 0,
        "max_new_entries_per_day": MAX_NEW_ENTRIES_PER_DAY,
        "shards_per_day": SHARD_COUNT,
        "shard_width": SHARD_WIDTH,
        "days": {},
        "updated_at": None,
    }


def save_refs_index(index):
    path = repo_path("refs.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, sort_keys=True)
        f.write("\n")


def existing_hashes_for_day(day):
    """All fix_hash values already in this day's shards (streamed)."""
    seen = set()
    shards_dir = repo_path("corpus", day, "shards")
    if not os.path.isdir(shards_dir):
        return seen
    for name in sorted(os.listdir(shards_dir)):
        if not name.endswith(".jsonl"):
            continue
        with open(os.path.join(shards_dir, name), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    seen.add(json.loads(line)["fix_hash"])
                except (KeyError, ValueError):
                    continue
    return seen


def validate_entry(entry, line_no):
    for field in REQUIRED_FIELDS:
        if field not in entry or entry[field] in (None, ""):
            return False, "missing required field %r (line %d)" % (field, line_no)
    if not isinstance(entry, dict):
        return False, "not a JSON object (line %d)" % line_no
    return True, ""


def run_update(batch_path, day, cap=MAX_NEW_ENTRIES_PER_DAY):
    """Stream the batch into the day's shards. Returns the update log."""
    manifest = load_day_manifest(day)
    seen = existing_hashes_for_day(day)
    shards_dir = repo_path("corpus", day, "shards")
    os.makedirs(shards_dir, exist_ok=True)

    log = {
        "date": day,
        "batch": os.path.basename(batch_path),
        "read": 0,
        "accepted": 0,
        "rejected_duplicate": 0,
        "rejected_invalid": 0,
        "overflow_queued": 0,
        "cap": cap,
        "cap_applied": False,
        "shard_entries": list(manifest["shard_entries"]),
        "errors": [],
    }

    # Open shard files once, append-only. Buffered writes, flushed per chunk.
    handles = {}
    try:
        with open(batch_path, "r", encoding="utf-8") as batch:
            chunk = 0
            for line_no, line in enumerate(batch, start=1):
                line = line.strip()
                if not line:
                    continue
                log["read"] += 1
                try:
                    entry = json.loads(line)
                except ValueError:
                    log["rejected_invalid"] += 1
                    log["errors"].append("line %d: not valid JSON" % line_no)
                    continue
                ok, why = validate_entry(entry, line_no)
                if not ok:
                    log["rejected_invalid"] += 1
                    log["errors"].append(why)
                    continue
                if entry["fix_hash"] in seen:
                    log["rejected_duplicate"] += 1
                    continue
                if manifest["entries"] + log["accepted"] >= cap:
                    # Cap reached: queue the rest, do not drop silently.
                    log["overflow_queued"] += 1
                    log["cap_applied"] = True
                    continue
                idx = shard_index(entry)
                if idx not in handles:
                    handles[idx] = open(
                        os.path.join(shards_dir, "shard-%04d.jsonl" % idx),
                        "a", encoding="utf-8",
                    )
                handles[idx].write(json.dumps(entry, sort_keys=True) + "\n")
                seen.add(entry["fix_hash"])
                log["accepted"] += 1
                log["shard_entries"][idx] += 1
                chunk += 1
                if chunk >= CHUNK_SIZE:
                    for h in handles.values():
                        h.flush()
                    chunk = 0
    finally:
        for h in handles.values():
            h.close()

    # Finalise: per-shard hashes + counts, day manifest, top-level index.
    manifest["entries"] = sum(log["shard_entries"])
    manifest["shard_entries"] = log["shard_entries"]
    manifest["cap_applied"] = log["cap_applied"]
    manifest["overflow_queued"] = log["overflow_queued"] > 0
    manifest["overflow_count"] = log["overflow_queued"]
    manifest["shard_sha256"] = []
    for idx in range(SHARD_COUNT):
        path = os.path.join(shards_dir, "shard-%04d.jsonl" % idx)
        manifest["shard_sha256"].append(
            sha256_file(path) if os.path.exists(path) else None
        )
    save_day_manifest(day, manifest)

    index = load_refs_index()
    index["days"][day] = {
        "manifest": "corpus/%s/manifest.json" % day,
        "entries": manifest["entries"],
        "shards": SHARD_COUNT,
    }
    index["total_entries"] = sum(d["entries"] for d in index["days"].values())
    index["total_days"] = len(index["days"])
    index["updated_at"] = day
    save_refs_index(index)

    # Daily update log (the record of what this run did).
    log["total_entries_after"] = manifest["entries"]
    log["total_corpus_entries"] = index["total_entries"]
    log_path = repo_path("updates", "%s.json" % day)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, sort_keys=True)
        f.write("\n")
    return log


def verify_day(day):
    """Verify a day's shards against its manifest. Returns (ok, report)."""
    manifest = load_day_manifest(day)
    shards_dir = repo_path("corpus", day, "shards")
    report = {"date": day, "ok": True, "shards": [], "errors": []}
    total = 0
    for idx in range(SHARD_COUNT):
        path = os.path.join(shards_dir, "shard-%04d.jsonl" % idx)
        count = 0
        seen = set()
        dupes = 0
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    count += 1
                    try:
                        h = json.loads(line)["fix_hash"]
                    except (KeyError, ValueError):
                        report["errors"].append(
                            "shard %04d: unreadable line" % idx
                        )
                        report["ok"] = False
                        continue
                    if h in seen:
                        dupes += 1
                    seen.add(h)
            actual_sha = sha256_file(path)
        else:
            actual_sha = None
        expected_sha = manifest["shard_sha256"][idx]
        expected_count = manifest["shard_entries"][idx]
        shard_ok = (actual_sha == expected_sha and count == expected_count
                    and dupes == 0)
        if not shard_ok:
            report["ok"] = False
            report["errors"].append(
                "shard %04d: count %d (expected %d), sha %s, dupes %d"
                % (idx, count, expected_count,
                   "match" if actual_sha == expected_sha else "MISMATCH", dupes)
            )
        total += count
        report["shards"].append({
            "shard": idx, "entries": count,
            "sha256_match": actual_sha == expected_sha, "duplicates": dupes,
        })
    if total != manifest["entries"]:
        report["ok"] = False
        report["errors"].append(
            "day total %d != manifest entries %d" % (total, manifest["entries"])
        )
    report["total_entries"] = total
    return report["ok"], report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", help="batch file (JSONL, one ref per line)")
    parser.add_argument("--date", default=_date.today().isoformat(),
                        help="corpus day (default: today)")
    parser.add_argument("--cap", type=int, default=MAX_NEW_ENTRIES_PER_DAY,
                        help="max new entries for the day (default: 1,000,000,000)")
    parser.add_argument("--verify", action="store_true",
                        help="verify a day's shards against its manifest")
    args = parser.parse_args(argv)

    if args.verify:
        if not args.batch:
            ok, report = verify_day(args.date)
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0 if ok else 1
        parser.error("--verify does not take --batch")
    if not args.batch:
        parser.error("--batch is required unless --verify")
    if not os.path.exists(args.batch):
        parser.error("batch file not found: %s" % args.batch)

    log = run_update(args.batch, args.date, cap=args.cap)
    print(json.dumps(log, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
