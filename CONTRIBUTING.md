# Contributing

1. Fork the repository and create a focused branch.
2. Install Hermes Agent plus the development dependencies from `pyproject.toml`.
3. Add behavior-focused tests for every change.
4. Run:

```bash
pytest -q
ruff check .
```

Hardware tests are opt-in and must never contain real node IDs or channel keys in committed files. Run the smoke test with explicit arguments as documented in the README.

Please open an issue before implementing a new transport or changing the security model.
