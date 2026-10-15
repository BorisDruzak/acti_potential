# Encoding Rule

- Always write text files in `UTF-8` (without BOM).
- Never use default shell encoding for rewrites. For scripted edits, explicitly use `encoding='utf-8'`.
- If terminal encoding is uncertain, use Unicode escape sequences (`\uXXXX`) in one-off scripts.
- After text changes, run a quick check for mojibake patterns (`??`, `Р`, `С`, broken emoji) and fix before finishing.
