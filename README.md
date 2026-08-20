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

## Project conventions

`blex` manages one Blender Extension per project. Every command accepts an
optional `DIRECTORY` as the project root and uses the current working directory
when it is omitted. It does not search parent directories for a project or scan
the source tree for a manifest. The only manifest it manages is
`blender_manifest.toml` directly inside the resolved source directory.

Relative path options and TOML paths other than `DIRECTORY` are resolved from
the project root. Global options must appear before the command:

```console
blex [--version] [--verbose] <command>
```

Use `--verbose` to show resolved paths, executed commands, cache decisions, and
generated destinations.

## Initialize a project

```text
blex init [DIRECTORY]
    [--force]
    [--name NAME]
    [--description DESCRIPTION]
    [--python X.Y]
    [--blender EXECUTABLE]
    [--source-dir PATH]
```

For example:

```console
blex init my-extension --description "My Blender Extension"
```

The default project layout is:

```text
my-extension/
├── pyproject.toml
├── .python-version
├── README.md
├── LICENSE
├── .gitignore
└── src/
    └── my_extension/
        ├── blender_manifest.toml
        ├── __init__.py
        ├── operators.py
        └── panels.py
```

The directory name, or `--name` when provided, is used to derive the project
name, manifest ID, display name, generated class names, and default source
directory. This input may contain ASCII letters, digits, spaces, `-`, and `_`.
After separator normalization, the project name and manifest ID must begin with
an ASCII letter; a manifest ID also cannot be a Python keyword.

The generated `.python-version` is selected in this order:

1. `--python X.Y`
2. the Python major and minor version detected by `--blender EXECUTABLE`
3. an existing `.python-version`
4. `3.11`

When both `--python` and `--blender` are given, `--python` wins and Blender is
not started. `--blender` on `init` is only used for Python detection; it does not
replace the generated `[tool.blex].blender` setting.

An existing project marker requires `--force`. Forced initialization merges
missing configuration and template entries while preserving compatible existing
values, comments, README, license, and Extension source files. Explicit values
that conflict with existing project metadata are reported as errors.

## Configure a project

`blex` configuration lives only in the project root's `pyproject.toml`. A newly
initialized project contains the following settings, with `source-dir` adjusted
to its generated manifest ID:

```toml
[tool.blex]
source-dir = "src/my_extension"
blender = "blender"
pip = "pip"

[tool.blex.dev]
link-mode = "auto"
reload-panel = true
wheels = true
debugpy = false
debugpy-host = "127.0.0.1"
debugpy-port = 5678
# blend-file = "scene.blend"
# user-resources = "path/to/user-resources"
# python = "3.11"

[tool.blex.build]
output-dir = "dist"
wheels = true
# python = ["3.11"]
# platforms = ["windows-x64", "macos-arm64", "linux-x64"]
```

Values are selected in this order:

1. an explicitly supplied command-line option
2. the corresponding `pyproject.toml` setting
3. the built-in default

An explicit command-line list of Python versions or platforms replaces the
configured list. `blex` does not provide environment-variable overrides for its
own settings. Standard pip configuration and `PIP_*` environment variables are
inherited by the pip subprocess.

Unknown keys inside `[tool.blex]`, `[tool.blex.dev]`, or `[tool.blex.build]` are
errors. Unknown values in other project metadata and tool tables are preserved.
The source directory is selected from `--source-dir`, then
`[tool.blex].source-dir`, then `src/<manifest-id>`.

## Develop in Blender

```text
blex dev [DIRECTORY]
    [--source-dir PATH]
    [--blender EXECUTABLE]
    [--blend-file FILE]
    [--user-resources DIRECTORY]
    [--link-mode {auto,symlink,copy}]
    [--reload-panel | --no-reload-panel]
    [--debugpy | --no-debugpy]
    [--debugpy-host HOST]
    [--debugpy-port PORT]
    [--wheels | --no-wheels]
    [--python X.Y]
    [--pip EXECUTABLE]
    [--refresh-wheels]
    [-- <BLENDER_ARGS>...]
```

`dev` prepares an isolated Blender user-resources tree at
`.blex/runtime_environment`, installs the Extension in a local `blex_dev`
repository, enables it, and waits for Blender to exit. An optional
`--user-resources` directory is copied as the seed for that isolated tree on
every run; the parent process's Blender user directories are not used
implicitly.

Blender is resolved from `--blender`, then `[tool.blex].blender`, then the
command name `blender`. Other installation locations are not searched. Use
`--blend-file` to open a file and `--` to pass remaining arguments directly to
Blender:

```console
blex --verbose dev --blend-file scene.blend -- --debug
```

In the default `auto` link mode, `blex` first deploys the source and wheels with
symbolic links. If any link cannot be created, it warns and rebuilds the whole
deployment using copies. Explicit `symlink` mode fails instead of falling back;
explicit `copy` mode copies from the start. Source edits are immediately visible
through symbolic links. Copy mode takes one snapshot at startup and does not
provide a file watcher or resynchronize while Blender is running.

The reload panel is enabled by default in Blender's sidebar under **Reload**.
Its **Reload** button disables the Extension, removes its loaded Python modules,
and enables it again. In copy mode this does not recopy the source.

debugpy is disabled by default. Enable its bundled server without waiting for a
client connection with:

```console
blex dev --debugpy --debugpy-host 127.0.0.1 --debugpy-port 5678
```

## Manage Python dependencies

Automatic Wheel collection is enabled by default for `dev` and `build`. Its
only dependency input is `[project].dependencies`; optional dependencies and
dependency groups are not collected. Dependencies must be index-resolvable PEP
508 requirements with binary Wheels. Direct URLs, VCS requirements, local
files, local directories, editable installs, requirements files, constraints
files, and pip-specific options are not supported.

`--no-wheels` disables automatic collection. Wheels explicitly listed in the
source manifest are still validated and included. Downloaded Wheels are cached
under `.blex/wheels`; use `--refresh-wheels` to re-resolve unchanged dependency
requirements, including after changing pip configuration or when a newer
unfixed release should be selected.

When automatic collection is enabled for `dev`, the target Python is selected
from `--python`, then `[tool.blex.dev].python`, then `.python-version`; only the
current host platform is targeted. For `build`, `--python` and `--platform` may
each be repeated. The corresponding configured arrays are used when no
command-line list is supplied, and `.python-version` plus the current host
platform are the final defaults.

Supported platform identifiers are:

- `windows-x64`
- `windows-arm64`
- `macos-x64`
- `macos-arm64`
- `linux-x64`

## Create staging trees

```text
blex build [DIRECTORY]
    [--source-dir PATH]
    [--output-dir PATH]
    [--wheels | --no-wheels]
    [--python X.Y]...
    [--platform PLATFORM]...
    [--pip EXECUTABLE]
    [--refresh-wheels]
```

For example, the following creates staging trees for two Python versions and
collects the union of the Windows x64 and macOS ARM64 Wheels for each one:

```console
blex build --python 3.11 --python 3.12 --platform windows-x64 --platform macos-arm64
```

The default output directory is `dist`, with one final source tree per Python
version:

```text
dist/
└── py3.11/
    └── source/
        ├── blender_manifest.toml
        ├── <Extension source entries>
        └── wheels/
```

Multiple platforms are combined in the same Python source tree rather than
split into platform directories. The generated manifest records the selected
platforms and merged Wheels. Only selected `pyX.Y` target directories are
replaced; unselected Python builds and other files in the output directory are
left unchanged.

The staging copy includes all content under the source directory, including
hidden files and `__pycache__`; manifest `paths_exclude_pattern` entries are not
used as copy filters. `blex build` does not start Blender, run Blender's Extension
build command, or create a ZIP archive.

## Contributing

```console
uv sync --locked --all-groups
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run pyrefly check
```

The implementation contract is documented in `PLAN.md`.

## License

blex is licensed under the GNU General Public License v3.0 or later. See
`LICENSE`.
