# Blender Extension Development CLI

## 1. Abstruct

この文書はBlender Extension開発に必要な処理を持つPython製CLIプロジェクトの仕様を記述する。

CLIとPythonパッケージの名称を`blex`とする。

## 2. Project Structure

blexが構成するBlender Extension向けプロジェクトのディレクトリ構造は概ね以下とする。

```text
my-extension/
├── pyproject.toml
├── .python-version
├── README.md
├── .gitignore
├── src/
│   └── my_extension/
│       ├── blender_manifest.toml
│       ├── __init__.py
│       ├── operators.py
│       └── panels.py
├── dist/
└── .blex/
    ├── .gitignore
    └── ...
```

## 3. Commands

### blex init

```bash
blex init [DIRECTORY] [OPTIONS]
```

ディレクトリに対してblexと開発Blender Extensionに必要な情報を初期化する。
主に記述するのは`pyproject.toml`と`blender_manifest.toml`である。
対象ディレクトリが既に存在する場合はデフォルトでエラーとする。強制オプションが明示された場合に限り、既存の構造や値を変更せず、不足するフィールドとファイルのみを追加する。

CLIのインターフェースはuv initを基準に合わせる。
ディレクトリはOptionalであり、未指定の場合現在のディレクトリに初期化を行う。

`blex init`の実行のみで環境が成立する。また、オプションで動作を変更することができる。

利用するPythonのバージョンは、デフォルトではBlender4.2で用いられているPython3.11とする。
`pyproject.toml`の`requires-python`フィールドは`>=3.11`として、実際のPythonバージョン固定は`.python-version`ファイルで行う。
固定するのはメジャー・マイナーバージョンまでで、マイクロバージョンは固定範囲外とする。
ちなみに、このPythonバージョンはあくまで開発中に想定するPythonのバージョンを指定するものであり、後述のWheel取得などで用いるPythonバージョンとは別である。

`blex init`のオプションを用いて、この指定するPythonバージョンを上書きすることができる。
以下の指定方法を持つ。両方が同時に指定された場合は、Pythonバージョンの直接指定を優先する。

- Pythonバージョンを直で指定するもの
- Blenderの実行バイナリパスを指定し、ランタイムを元にPythonバージョンを決定するもの

プロジェクト名はデフォルトでディレクトリ名として、オプションを通して名前を設定出来るものとする。
オプション名は`--name`とする。
ソース位置は`--source-dir`で指定でき、デフォルトは`src/<manifest-id>`とする。
バージョンはデフォルトで`0.1.0`とする。
authorsはデフォルトではプレースホルダとする。
descriptionはオプションで与えられる。デフォルトは`Add your description here`とする。
生成される`pyproject.toml`はおおよそ以下のようなものを想定している。

```toml
[project]
name = "project-name"
version = "0.1.0"
description = "Add your description here"
readme = "README.md"
authors = [
    { name = "Your Name", email = "you@example.com" }
]
requires-python = ">=3.11"
dependencies = []

[dependency-groups]
dev = [
    "blex>=0.1.0"
]
```

#### `blender_manifest.toml`仕様

id, nameはディレクトリ名を基準に設定する。
versionは1.0.0を初期値とする。
taglineやmaintainerはプレースホルダとする。
typeはadd-onである。

```
schema_version = "1.0.0"

id = "my_example_extension"
version = "1.0.0"
name = "My Example Extension"
tagline = "This is another extension"
maintainer = "Developer name"
type = "add-on"

# # Optional: link to documentation, support, source files, etc
# website = "https://extensions.blender.org/add-ons/my-example-package/"

# # Optional: tag list defined by Blender and server, see:
# # https://docs.blender.org/manual/en/latest/advanced/extensions/tags.html
# tags = ["Animation", "Sequencer"]

blender_version_min = "4.2.0"
# # Optional: Blender version that the extension does not support, earlier versions are supported.
# # This can be omitted and defined later on the extensions platform if an issue is found.
# blender_version_max = "5.1.0"

# License conforming to https://spdx.org/licenses/ (use "SPDX: prefix)
# https://docs.blender.org/manual/en/latest/advanced/extensions/licenses.html
license = [
  "SPDX:GPL-3.0-or-later",
]
# # Optional: required by some licenses.
# copyright = [
#   "2002-2024 Developer Name",
#   "1998 Company Name",
# ]

# # Optional: list of supported platforms. If omitted, the extension will be available in all operating systems.
# platforms = ["windows-x64", "macos-arm64", "linux-x64"]
# # Other supported platforms: "windows-arm64", "macos-x64"

# # Optional: bundle 3rd party Python modules.
# # https://docs.blender.org/manual/en/latest/advanced/extensions/python_wheels.html
# wheels = [
#   "./wheels/hexdump-3.3-py3-none-any.whl",
#   "./wheels/jsmin-3.0.1-py3-none-any.whl",
# ]

# # Optional: add-ons can list which resources they will require:
# # * files (for access of any filesystem operations)
# # * network (for internet access)
# # * clipboard (to read and/or write the system clipboard)
# # * camera (to capture photos and videos)
# # * microphone (to capture audio)
# #
# # If using network, remember to also check `bpy.app.online_access`
# # https://docs.blender.org/manual/en/latest/advanced/extensions/addons.html#internet-access
# #
# # For each permission it is important to also specify the reason why it is required.
# # Keep this a single short sentence without a period (.) at the end.
# # For longer explanations use the documentation or detail page.
#
# [permissions]
# network = "Need to sync motion-capture data to server"
# files = "Import/export FBX from/to disk"
# clipboard = "Copy and paste bone transforms"

# # Optional: advanced build settings.
# # https://docs.blender.org/manual/en/latest/advanced/command_line/extension_arguments.html#command-line-args-extension-build
# [build]
# # These are the default build excluded patterns.
# # You only need to edit them if you want different options.
# paths_exclude_pattern = [
#   "__pycache__/",
#   "/.git/",
#   "/*.zip",
# ]
```

### blex dev

```bash
blex dev [DIRECTORY] [OPTIONS]
```

ディレクトリのプロジェクトについて、開発のためにBlenderを実行し、Blender終了まで親CLIがプロセスを監督する。

開発体験のため、以下のような機能を想定する。
blenderの起動時スクリプトを指定することにより、当該ExtensionのReloadを行うPanelを追加する機能を持つ。この機能はオプションで無効化できる。
Reload Panelは、Blender起動時に実行されるbootstrap scriptによってoperatorとpanelの形でExtensionによらず直接登録される。

また、Blender内Pythonでdebugpyによるデバッグサーバーを起動する機能を有する。この機能はデフォルトで無効とし、オプションで明示的に有効化する。
debugpyの導入は、必要になった時にCLIに同梱したUniversal Wheelのdebugpy 1.8.21を`.blex/debugpy/1.8.21`ディレクトリにzipfileを用いて展開する。
そして、Blenderの起動スクリプトにおいてそのディレクトリを sys.path に追加する方式で行う。

オプションとして、Blender起動時に`BLENDER_USER_RESOURCES`環境変数で渡される、Blenderのユーザー領域ディレクトリを指定することができる。
指定されたディレクトリは直接参照されるのではなく、`blex dev`起動時に毎回複製され、起動されるたび同じ初期状態を保つ。
指定がない場合は`.blex`以下に空ディレクトリとして作成される。
extensionは、デフォルトではそのユーザー領域ディレクトリ中の`extensions/user_default`以下に開発中ファイルをリンクされることによってBlenderに認識される。
リンクを用いることで変更の自動適用を可能にする。
リンクではなく、extensionのファイルを丸ごとコピーすることもオプションにより可能とする。
デフォルトでは、リンクの手段として以下のような選択順を考える。

```text
macOS/Linux:
  symlink
  copy

Windows:
  symlink
  junction
  copy
```

Blender ExtensionのWheel対応に則り、`blex dev`実行時にwheelを集め、環境にリンクする機能を持つ。これはオプションで無効化できる。
起動時、pyproject.tomlにおける`project.dependencies`の記述を元に、必要なwheelを自動的に集める。
このとき想定するPythonのバージョンは環境のPythonバージョンとは別にオプションで直接指定できる。指定がない場合は`.python-version`を使用する。Blenderの実行バイナリからのPythonバージョン検出は行わない。

収集したWheelの実体は`.blex`ディレクトリ以下にキャッシュされ、`blex dev`実行時はリンクで配置される。

`blender_manifest.toml`についてWheelのパスを指定する必要があるが、実行時は原本は変更せずにwheelのパスを追加した版を作成しユーザー領域ディレクトリに配置するようにする。

デフォルトでは以下のようなディレクトリ構造となる。ディレクトリ名は例である。

```text
.blex/runtime_environment/extensions/user_default/
└── my_extension/
    ├── blender_manifest.toml
    ├── sourceへのリンク
    └── wheelsへのリンク
```

さらに、`blex dev`はオプションとして以下を指定できる。

- 作業する`.blend`プロジェクトファイル（デフォルト: デフォルトのBlender動作）
- debugpyのホスト、ポート

以下のオプションは、`pyproject.toml`内において後述の`blex build`コマンドと設定を共有する。

- 実行時に用いられるblender実行ファイルのパス（デフォルト: blenderにPATHが通っている場合、その実行環境のパス。無い場合はプレースホルダ）

オプションは、同等のものを`pyproject.toml`中に設定することでデフォルト動作を変更できる。値がない場合はデフォルト動作とする。
また、`blex init`による`pyproject.toml`生成時、こういったオプションはデフォルト値かプレースホルダで`[tool.blex]`中に記述する。
`pyproject.toml`内のオプション記述は、CLIでのオプション指定によって上書きされる。

### blex build

```bash
blex build [DIRECTORY] [OPTIONS]
```

ビルドはWheelの収集、`blender_manifest.toml`への追記、および各Pythonバージョン向けビルド用ディレクトリの作成までを行う。Blender標準のExtension buildコマンドは実行せず、stagingディレクトリは保持する。

`blex dev`と同様のロジックでWheelを収集する。これはオプションで無効化できる。
起動時、pyproject.tomlにおける`project.dependencies`の記述を元に、必要なwheelを自動的に集める。
このとき想定するPythonのバージョンは環境のPythonバージョンとは別にオプションで直接指定できる。指定がない場合は`.python-version`を使用する。Blenderの実行バイナリからのPythonバージョン検出は行わない。

Pythonバージョンは複数指定可能として、複数の場合はその分のディレクトリをそれぞれ作成しソースも複製して格納する。

また、複数のPlatform向けのWheelを収集する機能を持ち、複数指定することで複数PlatformのWheelを一度に収集できる。
これは、Pythonバージョンとは異なりBlender側にPlatformごとのビルドを作成する機能があるので、ディレクトリを分ける必要はない。オプションなしの場合は現在実行されているPlatform向けに収集する。

また、Pythonの環境は様々なため、収集時に用いるpipはパスで選択可能とする。デフォルトではPATHにあるものを素直に用いる。

ソース側の`blender_manifest.toml`は変更しない。
`blex dev`と同様、オプションで設定できる項目はpyproject.toml内でデフォルト値を変更できるものとする。

デフォルト時、build後は以下のようなディレクトリが作成されるものとする。
build-idはPythonバージョンのみから作成する。

```text
dist/<build-id>/
└── source/
    ├── blender_manifest.toml
    ├── Extensionソース
    └── wheels/
```



### 注意点

実装において、できる限り必要な時になって初めて環境やファイル・ディレクトリを作成する遅延初期化を心がける。

Blenderの実行ファイルから内部Pythonのバージョンを調査する処理は、毎回起動して調査していると無駄となる。
そのため、一度調査した実行ファイルはそのハッシュと共にPython環境を記録し、次からハッシュがヒットした際は記録を情報ソースとする。

読みやすさを重視してください。過剰なエラー処理は行わず、問題がある場合は実行を失敗するようにしてください。