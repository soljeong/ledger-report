# Agent Notes

This repository is intended to be publishable.

- Do not commit real customer/vendor workbooks, parsed production JSON, generated validation workbooks, or real reports.
- Keep private files in `private_sources/`, `private_intermediate/`, and `private_reports/`; these directories are ignored by Git.
- Use `examples/dummy_json/` for public tests, screenshots, and README examples.
- Keep generated HTML reports out of version control. Use `docs/assets/report-preview.png` for the public visual preview.
- Prefer command-line options over hard-coded local paths when adding new scripts.
- Before committing, run:

```bash
.venv/bin/python -m unittest discover -s . -p 'test_*.py'
```
