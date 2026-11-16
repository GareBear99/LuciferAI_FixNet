# FixNet Corpus — design

**The corpus IS FixNet** (Gary, 2026-11-10, twice). The corpus of fixes and
knowledge is FixNet itself — not a corpus that FixNet feeds. This repo is
the corpus: the community fixes/knowledge, the index, and the daily update
that grows it.

**Kept separate from the LLMBuilder training corpus.** There are two
corpora and they never mix: (a) the LLMBuilder training corpus — 2,426
plaintext SFT pairs in `GareBear99/ARC-Neuron-LLMBuilder`, fed at most
monthly at retrain time; (b) this repo — the FixNet corpus, encrypted
`.enc` patches + `.sig` signatures + public metadata index. A fix that
lands here does not land in the training corpus unless a separate,
deliberate step formats it as a corpus row. Nothing in this repo writes
to the training corpus.

---

## The billion — what they are really for

**The corpus is capped at growing 1,000,000,000 entries a day, and it can
reach that cap from daily activity alone — even if no users sign up**
(Gary, 2026-11-16). The growth does not depend on new signups. It comes
from the daily flow itself: the temps, the fixes, the pass.

That is what the billion are really for. The 1B/day cap on the daily grab
is the corpus's daily growth cap. The grab verifies up to a billion temps
a day; the verified population's fixes and knowledge flow up through
FixNet and land here as the day's corpus update. The billion are the
corpus's daily feed, not just a verification number.

Consequences for this repo (built into the update script, not assumed):

- **The update must absorb up to 1B new entries in a single daily run
  without breaking.** The indexing and storage are built for that cap.
  Growth is not assumed to come only from new users.
- **The cap is enforced, never silently exceeded.** If a day's batch
  would pass 1B new entries, the excess queues for the next day
  (Gary, 2026-11-16 — cap overflow queues; Paperspace sorts the queue,
  the pass sets it). The overflow is never dropped and never forced in.
- **Memory is O(chunk), not O(corpus).** The update streams the batch
  and appends to shards. It never loads the corpus into memory.

---

## The day, in order

Two daily events, separate — do not conflate them (Gary, 2026-11-13).

| Time (Gary's time) | Event | What happens here |
|---|---|---|
| **12am** | The single daily grab + the FixNet commit riding it | The grab verifies temps (1 ping call, 0 neurons). The day's fix batch — staged by the client-side collector, kept by the smart filter — rides the grab up to this repo. This script (`scripts/daily-corpus-update.py`) lands the batch in the corpus and re-indexes it. One commit per day max. |
| **2am** | The repo's daily update on the Cloudflare neurons | The repo's community update of knowledge. Separate from the 12am grab and the commit riding it. Mechanics and cost are D15 (Gary's call) — this repo does not invent them. |

**Cost: 0 neurons.** FixNet is client to repo — data moving, not AI
(Gary, 2026-11-16). The pass costs nothing but the daily pull. Neurons
are for the AI work (LLMBuilder), not for FixNet.

**The pay:** the daily pull is the pay (Gary, 2026-11-16). Serving is
covered by the daily pull mechanism itself — the yield that comes down
is the pay.

**The SP cap:** the verified SP balance cap is the user's on-hand SP
balance (Gary, 2026-11-16). The daily pass draws from what they have on
hand; when they recharge, they have more to use. There is no separate
fixed cap.

---

## Layout

```
├── refs.json                  # top-level index MANIFEST (v2) — not the entry list
├── corpus/
│   └── <YYYY-MM-DD>/
│       ├── manifest.json      # day manifest: entries, per-shard counts + SHA-256
│       └── shards/
│           ├── shard-0000.jsonl   # one ref entry per line, append-only
│           └── ...
│               shard-0031.jsonl
├── fixes/                     # encrypted patch files (.enc)
├── signatures/                # SHA256 signatures (.sig)
├── scripts/
│   └── daily-corpus-update.py # the daily update (streaming, sharded, capped)
└── updates/
    └── <YYYY-MM-DD>.json      # daily update log (what each run did)
```

### Sharding — mirrors the grab

- **32 shards per day, tick-range width 31,250,000.**
  32 × 31,250,000 = 1,000,000,000 — the billion. The grab's shard N
  produces this day's corpus shard N. An entry's shard is
  `tick // 31,250,000` (clamped to 31). Entries with no tick are sharded
  by hash of `fix_hash`, so every entry is placeable and placement is
  deterministic.
- **Shard files are JSONL** (one ref entry per line), append-only. A
  shard can be read, verified, or served without touching the other 31.
- **The day manifest** records per-shard entry counts and SHA-256, the
  day total, and whether the cap was applied / overflow queued.
- **`refs.json` (v2)** is the top-level index manifest: total entries,
  total days, and per-day pointers to manifests. It is not the entry
  list — a flat entry list cannot hold a billion entries a day.

### Why not the original flat refs.json

The original uploader loaded the whole `refs.json` into memory, appended
one entry, and rewrote the whole file. That is the pattern that breaks:
at a billion entries a day the file is gigabytes, loading it is
impossible, and rewriting it daily is not a design. The v2 manifest
keeps `refs.json` as the file everyone knows, but as an index — the
entries live in the shards. **The entry shape itself is unchanged** from
the original: `fix_hash`, `user_id`, `timestamp`, `error_type`,
`script`, `encrypted_file`, `signature_file`, plus optional branching
fields (`inspired_by`, `variation_reason`, `relationship_type`).
Only the container is sharded, not the data.

### refs.json v2 shape

```json
{
  "version": 2,
  "format": "fixnet-refs-v2",
  "total_entries": 0,
  "total_days": 0,
  "max_new_entries_per_day": 1000000000,
  "shards_per_day": 32,
  "shard_width": 31250000,
  "days": {
    "2026-11-16": {
      "manifest": "corpus/2026-11-16/manifest.json",
      "entries": 0,
      "shards": 32
    }
  },
  "updated_at": "2026-11-16"
}
```

A reader that wants entries: `refs.json` → `days[date].manifest` →
`corpus/<date>/manifest.json` → `shards/shard-NNNN.jsonl`. A legacy v1
flat array is only accepted if it is empty (`[]`, the state this repo
has always been in); a non-empty v1 array must be migrated by hand
before a sharded update runs.

---

## The daily update — how to run it

```bash
# Land a day's staged batch (JSONL, one ref entry per line):
python3 scripts/daily-corpus-update.py --batch /path/to/batch-2026-11-16.jsonl --date 2026-11-16

# Verify a day's shards against its manifest (counts + SHA-256 + no dupes):
python3 scripts/daily-corpus-update.py --verify --date 2026-11-16
```

What the script does, in order:

1. Streams the batch line by line (chunked at 200,000, matching
   `GRAB_CHUNK_RECORDS`). Never loads the batch or the corpus into
   memory.
2. Validates each entry (required: `fix_hash`, `error_type`,
   `timestamp`). Invalid lines are rejected and recorded, not fatal.
3. Dedupes by `fix_hash` within the day and against the day's existing
   shards. Re-running the same batch adds nothing twice — the update
   is idempotent, and a partial/interrupted run can be re-run safely.
4. Places each entry in its shard (tick-range, hash fallback) and
   appends it to the shard JSONL file.
5. Enforces the 1B/day cap. Past the cap, entries are counted as
   overflow and queued for the next day — never dropped, never forced.
6. Writes the day manifest (per-shard counts + SHA-256), updates
   `refs.json`, and writes the daily update log to
   `updates/<YYYY-MM-DD>.json` (read / accepted / duplicates /
   invalid / overflow / totals).

The batch itself comes from the client-side collector + smart filter
(Phases 2–3 of the build plan) and rides the grab (Phase 5). This
script is the landing side — it assumes the batch is already staged
and filtered, and it does not fetch, filter, or verify fixes itself.

---

## What is still open (Gary's call — not decided here)

- **D1 — corpus, community repo, or both?** Whether the daily commit
  feeds the LLMBuilder training corpus, this repo, or both. This repo
  is the FixNet corpus either way; D1 decides whether a second,
  separate commit path also formats rows for training.
- **D3 — where the daily pass runs, and who commits?** Local commit via
  the HUB token route, or the commit rides the grab on a server. The
  encrypted community path cannot go through the server in plaintext —
  the client encrypts first and hands over ciphertext.
- **D9 — does a FixNet commit consume any of the 1-billion cap?** The
  grab plan assumes the cap is temps-only and the commit rides free.
  Gary confirms or corrects. (The 1B/day corpus growth cap in this doc
  is the corpus's own cap, not the grab's acceptance cap — the two are
  related, not the same number.)
- **D15 — the 2am update mechanics and cost.** What exactly is updated
  on the repo at 2am, which direction the knowledge flows, and what it
  costs. Not invented here.
- **The local-only rule lift.** The FixNet local-only rule (2026-11-04)
  still stands until Gary lifts it. Until then, everything that would
  move data over the connection is design and local-only prep.
- **D6 — revive this repo?** Answered by Gary's order to prep and
  update it: yes, this repo is the community destination, and the
  community starts at zero — the first commits through the grab are
  the seed.

---

## Scale note (honest)

The indexing layer handles the 1B/day cap by design: streaming
(O(chunk) memory), 32 shards per day, JSONL append-only shards, and
manifests that stay small (counts + hashes, never entries). What the
indexing layer does not by itself solve is the physical storage of a
full 1B-entry day in git — a billion metadata entries is on the order
of hundreds of gigabytes, and git/GitHub are not the store for that
volume. Typical days will be far smaller than the cap (fixes are a
subset of temp activity; the cap is a ceiling, not a target). If daily
volume ever approaches the cap, the shard files are the bulk and the
storage backend for them is a separate decision — the manifest/index
shape in this repo does not need to change to point at it.

---

## Sources

- Gary: "the corpus is fixnet" (2026-11-10, twice); "once we get the
  daily pass in and the fixnet uses it to the repo of fixnet then it is
  public" (2026-11-10); the 12am grab + 2am repo update, two daily
  events, separate (2026-11-13); the daily pull is the pay, Paperspace
  sorts the queue but the pass sets it, cap overflow queues, the SP cap
  is the on-hand balance, FixNet and the pass cost 0 neurons (2026-11-16);
  **the corpus is capped at growing a billion a day, potentially, even
  if no users sign up** (2026-11-16).
- `~/workspace/fixnet-build-plan-2026-11-13.md` (D1–D15, build order
  Phases 0–9), `~/workspace/fixnet-next-step.md` (2026-11-15),
  `~/workspace/dailies-fixnet-gates-2026-11-16.md` (settled gates).
- Original: `LuciferAI_Local/core/fixnet_uploader.py` (refs entry shape,
  Fernet + SHA-256), `fixnet_daemon.py` (append-only refs, conflict
  rule), `relevance_dictionary.py` (remote-refs load), `smart_upload_filter.py`
  (novelty 0.7). The code uses **Fernet (AES-128-CBC + HMAC-SHA256)**,
  not AES-256 — this repo's README previously said AES-256; the code
  is the truth.
