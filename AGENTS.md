# Repository guide

`PLAN.md` is the normative product specification. Do not introduce aliases,
compatibility paths, implicit project discovery, or silent fallback behavior that
is not defined there.

The package uses a `src` layout. Runtime code belongs in `src/blex`, templates and
vendored runtime assets must remain package resources, and tests belong in
`tests`. Keep runtime dependencies limited to `tomlkit` and the standard library.

Before handing off changes, run:

```console
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run pyrefly check
```

Generated project text is UTF-8 without BOM, uses LF line endings, and ends with
a newline. Tests must not contact external services or download Blender.
