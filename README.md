# 🌐 LuciferAI FixNet

**Encrypted Collaborative Fix Repository**

This repository stores encrypted, signed code fixes from the LuciferAI community. All fixes are AES-256 encrypted and SHA256 signed for security and integrity.

## 📁 Structure

```
├── fixes/          # Encrypted patch files (.enc)
├── signatures/     # SHA256 signatures (.sig)
└── refs.json       # Relevance dictionary (anonymized metadata)
```

## 🔒 Security

- **AES-256 Encryption**: All fix content is encrypted
- **SHA256 Signatures**: Every fix is cryptographically signed
- **Anonymized User IDs**: Hash-based identification (no personal info)
- **Metadata Only Public**: Error types and relevance scores visible, not actual code

## 🔄 How It Works

1. LuciferAI detects and fixes an error locally
2. Fix is encrypted with user's local key
3. Encrypted patch + signature uploaded here
4. `refs.json` updated with metadata (error type, keywords, hash)
5. Other users can search metadata and generate similar fixes

## 🤝 Contributing

Fixes are automatically contributed when using LuciferAI with FixNet enabled:

```bash
# In LuciferAI terminal
fixnet sync    # Pull latest fixes
fixnet stats   # View statistics
```

## 📊 Current Stats

- Fixes indexed: Check `refs.json`
- Error types covered: NameError, ImportError, SyntaxError, etc.
- Consensus algorithm: Hash-based deduplication

## 🔗 Related

- **Main Repo**: [GareBear99/LuciferAI_Local](https://github.com/GareBear99/LuciferAI_Local)
- **Documentation**: See `docs/FIXNET_GUIDE.md` in main repo

---

**Made with 🩸 by the LuciferAI Community**
