# Test corpus

Tiny real files for the parsing layer.

- `one-page.pdf` — a hand-assembled minimal valid PDF (catalog → pages → page →
  Helvetica text "Hello from sempipe"), ~600 bytes, xref offsets computed. The
  generation recipe lives in the git history of this file's introducing commit;
  regenerating it must keep the text `Hello from sempipe` (tests assert on it).
