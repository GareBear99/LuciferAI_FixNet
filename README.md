# 🌐 LuciferAI FixNet

**Encrypted Collaborative Fix Repository — the FixNet corpus**

This repository IS the FixNet corpus: the community fixes and knowledge,
the index, and the daily update that grows it. See [CORPUS.md](CORPUS.md)
for the full design.

## 📁 Structure

```
├── refs.json          # Top-level index MANIFEST (v2) — points at daily manifests, not the entry list
├── corpus/            # Daily corpus data, sharded
│   └── <YYYY-MM-DD>/
│       ├── manifest.json      # Day manifest: entries, per-shard counts + SHA-256
│       └── shards/            # shard-0000.jsonl … shard-0031.jsonl (one ref per line)
├── fixes/             # Encrypted patch files (.enc)
├── signatures/        # SHA256 signatures (.sig)
├── scripts/
│   └── daily-corpus-update.py # The daily corpus update (streaming, sharded, capped)
└── updates/           # Daily update logs (<YYYY-MM-DD>.json)
```

## 🔄 The daily corpus update

The corpus updates once a day, from the daily flow:

- **12am Gary's time** — the single daily grab verifies temps, and the
  day's fix batch (client-side collector + smart filter) rides the grab
  up to this repo. `scripts/daily-corpus-update.py` lands the batch in
  the corpus and re-indexes it. One commit per day max.
- **2am Gary's time** — the repo's daily update on the Cloudflare
  neurons: its community update of knowledge. Separate from the 12am
  grab and the commit riding it.

**The billion:** the corpus is capped at growing 1,000,000,000 entries
a day, and it can reach that cap from daily activity alone — even if no
users sign up. The growth comes from the daily flow (the temps, the
fixes, the pass), not from new signups. That is what the billion are
really for. The update is built to absorb a billion new entries in a
single daily run: streaming (never loads the corpus into memory), 32
shards per day mirroring the grab (tick-range width 31,250,000), JSONL
append-only shards, and small manifests. Past the cap, the excess
queues for the next day — never dropped, never forced.

**Cost: 0 neurons.** FixNet is client to repo — data moving, not AI.
The pass costs nothing but the daily pull. The daily pull is the pay.

```bash
# Land a day's staged batch (JSONL, one ref entry per line):
python3 scripts/daily-corpus-update.py --batch batch-2026-11-16.jsonl --date 2026-11-16

# Verify a day's shards against its manifest:
python3 scripts/daily-corpus-update.py --verify --date 2026-11-16
```

## 🔒 Security

- **Fernet encryption**: All fix content is encrypted with Fernet
  (AES-128-CBC + HMAC-SHA256) under a per-device key that never leaves
  the device. (Earlier docs said AES-256; the code uses Fernet — the
  code is the truth.)
- **SHA256 Signatures**: Every fix is cryptographically signed
- **Anonymized User IDs**: Hash-based identification (no personal info)
- **Metadata Only Public**: Error types and relevance scores visible, not actual code

## 🔄 How It Works

1. LuciferAI detects and fixes an error locally
2. Fix is encrypted with the user's local key (Fernet)
3. The day's batch of fixes rides the daily grab up to this repo
4. The daily corpus update lands the batch in the corpus shards and
   re-indexes `refs.json` (the manifest)
5. Other users can search the metadata and generate similar fixes

## 🤝 Contributing

Fixes are contributed automatically when using Lucid with FixNet enabled,
once the local-only rule is lifted (Gary's call) and the connection is
built. Until then, the repo is local-only prep.

```bash
# In Lucid terminal (once the connection exists)
fixnet sync    # Pull latest fixes
fixnet stats   # View statistics
```

## 📊 Current Stats

- Fixes indexed: Check `refs.json` (v2 manifest: `total_entries`)
- Error types covered: NameError, ImportError, SyntaxError, etc.
- Consensus algorithm: Hash-based deduplication; weighted consensus
  (success 50%, network 20%, context 15%, recency 15%) in Lucid

## 🔗 Related

- **Main Repo**: [GareBear99/LuciferAI_Local](https://github.com/GareBear99/LuciferAI_Local)
- **Lucid**: the TypeScript FixNet core (`electron/core/fixnet/`)
- **Design**: [CORPUS.md](CORPUS.md) — the corpus, the billion, the daily update
- **Documentation**: See `docs/FIXNET_GUIDE.md` in the main repo

---

**Made with 🩸 by the LuciferAI Community**
