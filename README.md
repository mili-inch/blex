# blex

`blex` is a command-line tool for creating, developing, and staging Blender
Extensions. It keeps development state under `.blex`, resolves binary wheels for
Blender's supported targets, and never requires Blender for a release build.

## Requirements

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/) for contributor setup
- Blender 4.2 or newer when running `blex dev`

## Install and run

```console
uv tool install .
blex --help
blex init my-extension
cd my-extension
blex dev
blex build
```

Run `blex <command> --help` for all command-specific options. `blex build`
creates per-Python staging trees under `dist/`; packaging those trees as final
Blender Extension archives is deliberately left to the caller.

## Contributing

```console
uv sync --locked --all-groups
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run pyrefly check
```

The implementation contract is documented in `PLAN_new.md`.

## License

blex is licensed under the GNU General Public License v3.0 or later. See
`LICENSE`.
