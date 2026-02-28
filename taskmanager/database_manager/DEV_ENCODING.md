# Encoding Rules

- Project frontend/source files (`.html`, `.js`, `.css`) are stored as `UTF-8 with BOM`.
- Do not save these files in `cp1251`, `UTF-8 without BOM`, or mixed encodings.
- If you see text like `РќРµ ...`, treat it as mojibake and fix encoding immediately.

## Verification

Run from repository root:

```bash
python tools/check_encoding_mojibake.py
```

The command returns non-zero exit code if:

- BOM is missing.
- Typical mojibake patterns are detected.

