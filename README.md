# blex

`blex` is a command-line tool for creating, developing, and staging Blender
Extensions. It keeps development state under `.blex`, resolves binary wheels for
the running Python environment, and creates release staging trees without Blender.

## Requirements

- Python 3.11 or newer
- Blender 4.2 or newer when running `blex dev`
- `pip` installed in the same Python environment as blex for automatic Wheel collection

## Install and run

```console
python -m pip install .
blex --help
blex init my-extension
cd my-extension
blex dev
blex stage
```

Run `blex <command> --help` for all command-specific options. `blex stage`
creates a staging tree for the running Python and platform at `dist/source/`; packaging it as a final
Blender Extension archive is deliberately left to the caller.

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

`.python-version` helps select your development or CI environment. `dev` and
`stage` use the interpreter running blex and do not read that file. Run blex in
a Python environment matching the intended Blender Python and architecture.

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

[tool.blex.dev]
link-mode = "auto"
reload-panel = true
wheels = true
debugpy = false
debugpy-host = "127.0.0.1"
debugpy-port = 5678
# blend-file = "scene.blend"
# user-resources = "path/to/user-resources"

[tool.blex.stage]
output-dir = "dist"
wheels = true
```

Values are selected in this order:

1. an explicitly supplied command-line option
2. the corresponding `pyproject.toml` setting
3. the built-in default

`blex` does not provide environment-variable overrides for its
own settings. Standard pip configuration and `PIP_*` environment variables are
inherited by the pip subprocess.

Unknown keys inside `[tool.blex]`, `[tool.blex.dev]`, or `[tool.blex.stage]` are
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

In symlink mode, Reload also validates and synchronizes added, removed, and
renamed source entries. Generated manifests, wheels, and runtime-generated files
are preserved; unsafe links or collisions stop Reload before disabling the add-on.

The reload panel is enabled by default in Blender's sidebar under **Reload**.
Its **Reload** button disables the Extension, removes its loaded Python modules,
and enables it again. In copy mode this does not recopy the source.

debugpy is disabled by default. Enable its bundled server without waiting for a
client connection with:

```console
blex dev --debugpy --debugpy-host 127.0.0.1 --debugpy-port 5678
```

## Manage Python dependencies

Automatic Wheel collection is enabled by default for `dev` and `stage`. Its
only dependency input is `[project].dependencies`; optional dependencies and
dependency groups are not collected. Dependencies must be index-resolvable PEP
508 requirements with binary Wheels. blex runs `sys.executable -m pip download`
to resolve direct and transitive dependencies, markers, extras, and compatible
Wheel tags in the current environment. It only downloads Wheels; source builds
are disabled. pip is required only when automatic collection has dependencies.
Configure private indexes using standard pip settings. Do not override pip's
target environment or disable its dependency resolution through those settings.
Direct URLs, VCS requirements, local files, local directories, editable installs,
requirements files, constraints files, and pip-specific options in dependencies
are not supported.

`--no-wheels` disables automatic collection. Wheels explicitly listed in the
source manifest are still validated and included. Downloaded Wheels are cached
under `.blex/wheels/<fingerprint>/`. The fingerprint includes dependency inputs
and the execution environment, so different environments keep separate cache
entries. Locks use `.blex/locks/wheels/<fingerprint>.lock`. Deployments reference
immutable copies under
`.blex/wheel-objects`, so refreshing the cache cannot break a running Blender
session. These objects are retained until `.blex` is manually removed with all
dev/stage processes stopped. Use `--refresh-wheels` to re-resolve unchanged dependency
requirements, including after changing pip configuration or when a newer
unfixed release should be selected.

Both commands target the running Python, OS, and architecture. To build for
another environment, run blex in that environment, for example in a CI matrix.

Supported platform identifiers are:

- `windows-x64`
- `windows-arm64`
- `macos-x64`
- `macos-arm64`
- `linux-x64`

## Create staging trees

```text
blex stage [DIRECTORY]
    [--source-dir PATH]
    [--output-dir PATH]
    [--wheels | --no-wheels]
    [--refresh-wheels]
```

Run `blex stage` in each target environment. The output layout is:

```text
dist/
└── source/
    ├── blender_manifest.toml
    ├── <Extension source entries>
    └── wheels/
```

Only `<output-dir>/source/` is replaced, as one transaction. Other files and
directories in the output directory are preserved. Use separate workspaces or
`--output-dir` values to keep multiple environments' outputs. The generated
manifest declares one platform. Native Wheel compatibility comes from the executing environment;
choose CI runners or containers compatible with your distribution's minimum OS
requirements and Blender's Python and architecture.

The staging copy includes all content under the source directory, including
hidden files and `__pycache__`; manifest `paths_exclude_pattern` entries are not
used as copy filters. `blex stage` does not start Blender, run Blender's Extension
build command, or create a ZIP archive.

## Package builds and CI

blex uses Hatchling as its PEP 517 build backend. Build its sdist and Wheel with:

```console
python -m pip install build
python -m build
```

The repository CI uses `actions/setup-python` and pip. Its Windows x64, macOS x64,
and Linux x64 matrix runs Python 3.11 and 3.14 tests, stages a sample Extension,
and uploads each environment's source tree separately. A package job builds and
uploads the blex distributions. For an Extension project's CI, select the
required runner/Python pairs, install blex into each environment, run
`python -m blex stage`, and collect `dist/source/`.

## Contributing

uv is optional tooling for the locked contributor environment:


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
