# BLEX — Blender Extension開発CLI 仕様書

## 1. 概要

### 1.2 目的

BLEXは、Blender Extension開発に必要な以下の処理を統合するPython製CLIである。

- `blender_manifest.toml`の生成・検証
- Blender内部Pythonと整合する開発用Python環境の構築
- Blender API型スタブの導入
- プロジェクト専用Blenderユーザー環境の宣言・同期
- Supplemental Extensionの宣言・導入
- Python wheelを含むExtension ZIPのビルド
- 複数OS・CPUアーキテクチャ向けビルド
- debugpyを使用したBlender内部デバッグ
- 開発ソースのリンク
- 起動スクリプトによるReload OperatorおよびPanelの登録

---

# 5. `pyproject.toml`

設定例を以下に示す。

```toml
[project]
name = "my-extension-dev"
version = "0.0.0"
requires-python = ">=3.11,<3.12"
dependencies = []

[dependency-groups]
dev = [
    "fake-bpy-module-4.5",
    "pytest",
    "ruff",
]

blender-runtime = [
    "requests>=2.32",
]

[tool.blex]
schema-version = 1
source-dir = "extension"
dist-dir = "dist"
runtime-dependency-group = "blender-runtime"
environment-path = "./.blex/environment"

[tool.blex.target]
blender-version = "4.5"
python-version = "3.11"
python-abi = "cp311"

[tool.blex.stubs]
provider = "fake-bpy-module"
blender-version = "4.5"

[tool.blex.build]
bundle-wheels = true
platforms = ["current"]

[tool.blex.dev]
bundle-wheels = true
reload-panel = true
link-mode = "auto"

[tool.blex.dev.debug]
host = "127.0.0.1"
port = 5678
wait-for-client = false
```

BLEX固有設定はすべて`tool.blex`以下へ保存する。

---

# 6. 共通動作

## 6.2 Blender Probe

Blenderをbackground起動し、以下を取得する。

- Blenderバージョン
- Blender内部Pythonのバージョン
- Python ABI
- OS
- CPUアーキテクチャ

取得結果は診断およびキャッシュのため`.bext/state.toml`へ保存できる。

`.bext/state.toml`は環境構築の原本として使用しない。

# 7. `blex create`

## 7.1 構文

```bash
blex create [NAME] [OPTIONS]
```

主要オプション:

```text
--template TEMPLATE
--blender PATH
--stub PROVIDER
--stub-blender-version VERSION
--config-mode clean|clone
--scripts-mode clean|clone
--datafiles-mode clean|clone
--extensions-mode clean|clone
--no-input
--force
```

## 7.2 実行内容

1. 新規プロジェクトディレクトリを作成する。
2. Blender実行ファイルを解決する。
3. Blender Probeを実行する。
4. `uv init --bare`を実行する。
5. Blender Extensionテンプレートを生成する。
6. `blender_manifest.toml`を生成する。
7. Blender内部Pythonと一致するPython要件を設定する。
8. `.python-version`を生成する。
9. Blender API型スタブをdev dependencyとして追加する。
10. CONFIG、SCRIPTS、DATAFILES、EXTENSIONSのモードを保存する。
11. 空の`blex.lock`を生成する。
12. `uv lock`および`uv sync`を実行する。
13. 内部的に`blex sync`相当の処理を実行する。
14. Blenderをbackground起動して生成環境を検証する。

---

# 8. `blex init`

## 8.1 構文

```bash
blex init [PATH] [OPTIONS]
```

主要オプションは`blex create`と共通とする。

## 8.2 実行内容

既存ディレクトリをBLEXプロジェクトとして初期化する。

- `pyproject.toml`がない場合は`uv init --bare`を実行する。
- 既存の`pyproject.toml`を保持したまま`tool.blex`を追加する。
- 既存の`blender_manifest.toml`を読み込み、検証する。
- Blender内部Pythonと`requires-python`の整合性を確認する。
- 型スタブをdev dependencyとして追加する。
- CONFIG、SCRIPTS、DATAFILES、EXTENSIONSのモードを保存する。
- `blex.lock`が存在しない場合は生成する。
- 最後に`blex sync`相当の処理を実行する。

既存のPython要件を変更する必要がある場合、対話モードでは確認する。

非対話モードでは以下の明示指定が必要となる。

```text
--update-python-requirement
```

---

# 9. Pythonバージョン

Blender内部Pythonのmajor/minorへプロジェクトPythonを合わせる。

Blender内部Pythonが3.11の場合:

```toml
[project]
requires-python = ">=3.11,<3.12"
```

`.python-version`:

```text
3.11
```

patch versionは固定しない。

`uv`が対応Pythonを取得可能な場合は、managed Pythonを使用できる。

---

# 10. Blender API型スタブ

## 10.1 対応プロバイダー

```text
none
fake-bpy-module
blender-python-stubs
```

## 10.2 対象バージョン

既定では開発対象Blenderと同じmajor/minorを使用する。

対話またはオプションによって別バージョンを指定できる。

```text
--stub-blender-version VERSION
```

## 10.3 dependency group

型スタブはruntime dependencyに含めず、dev dependencyとして追加する。

```toml
[dependency-groups]
dev = [
    "fake-bpy-module-4.5",
]
```

---

# 21. `blex build`

## 21.1 構文

```bash
blex build [OPTIONS]
```

オプション:

```text
--without-wheels
--platform PLATFORM
--all-platforms
--skip-unavailable-platforms
--refresh-wheels
--output-dir PATH
--clean
```

`--platform`は複数指定可能とする。

## 21.2 既定動作

```bash
blex build
```

既定値:

```text
wheel bundle: 有効
platform: current
output: dist/
```

wheelを含めない場合:

```bash
blex build --without-wheels
```

## 21.3 対応platform

初期版では以下を扱う。

```text
windows-x64
windows-arm64
macos-x64
macos-arm64
linux-x64
```

現在のplatform向け:

```bash
blex build
```

指定platform向け:

```bash
blex build \
  --platform windows-x64 \
  --platform macos-arm64 \
  --platform linux-x64
```

全platform向け:

```bash
blex build --all-platforms
```

## 21.4 platform決定の優先順位

1. `--all-platforms`
2. `--platform`
3. `[tool.blex.build].platforms`
4. `current`

Primary Extensionのmanifestにplatform制約がある場合、build対象はその範囲内に限定する。

## 21.5 runtime dependency

以下のdependency groupをExtension runtime dependencyとして使用する。

```toml
[dependency-groups]
blender-runtime = [
    "requests>=2.32",
]
```

build時は`uv.lock`に固定されたバージョンを使用する。

## 21.6 wheel bundle

各targetについて、以下と互換性のあるwheelをpackage indexから取得する。

- Blender内部Python major/minor
- Python ABI
- OS
- CPUアーキテクチャ

対応対象:

- `py3-none-any`
- 対象Pythonと互換性のあるpure Python wheel
- 対象ABI・platformと一致するnative wheel

初期版ではsdistからnative wheelをビルドしない。

## 21.7 クロスplatform build

実行中OSと異なるplatformでも、必要なすべてのwheelがpackage indexに存在する場合はbuild可能とする。

target向けwheelを実行せず、取得・検証・パッケージングのみを行う。

要求targetの依存関係に対応wheelが存在しない場合、そのtargetはbuild不可とする。

## 21.8 不足platform

既定では、要求targetが1つでもbuild不可の場合はコマンド全体を失敗させる。

```bash
blex build \
  --all-platforms \
  --skip-unavailable-platforms
```

`--skip-unavailable-platforms`指定時は、build可能なtargetのみを出力する。

少なくとも1つのtargetが成功した場合は成功終了する。

## 21.9 staging

```text
.bext/build/<build-id>/
├── source/
│   ├── blender_manifest.toml
│   ├── Extensionソース
│   └── wheels/
└── output/
```

ソース側の以下は変更しない。

```text
extension/blender_manifest.toml
extension/wheels/
```

## 21.10 platform別ZIP

複数platformが指定された場合はplatform別ZIPを生成する。

```text
dist/
├── my_extension-1.0.0-windows-x64.zip
├── my_extension-1.0.0-windows-arm64.zip
├── my_extension-1.0.0-macos-x64.zip
├── my_extension-1.0.0-macos-arm64.zip
└── my_extension-1.0.0-linux-x64.zip
```

各ZIPには、そのplatformで必要なwheelだけを含める。

universal wheelは各ZIPへ含める。

## 21.11 `--without-wheels`

```bash
blex build --without-wheels
```

動作:

- runtime dependencyを解決しない。
- `wheels/`を生成しない。
- staging manifestから`wheels`を除外する。
- runtime dependencyが宣言されている場合は警告する。
- build自体は継続する。

---

# 22. `blex dev`

## 22.1 構文

```bash
blex dev [OPTIONS]
```

オプション:

```text
--debug
--debug-host HOST
--debug-port PORT
--wait-for-client
--without-reload-panel
--without-wheels
--link-mode auto|symlink|junction|copy
```

環境ディレクトリを指定するオプションは提供しない。

## 22.2 既定値

| 機能              | 既定値         |
| --------------- | ----------- |
| wheel bundle    | 有効          |
| Reload Panel    | 有効          |
| debugpy         | 無効          |
| link mode       | `auto`      |
| debug host      | `127.0.0.1` |
| debug port      | `5678`      |
| wait for client | 無効          |

## 22.3 起動前同期

`blex dev`は起動前に以下を検査する。

- `.bext/environment`の存在
- `.bext/state.toml`と設定の整合性
- `pyproject.toml`と`uv.lock`の整合性
- Supplemental Extension宣言と`blex.lock`の整合性
- managed repositoryの整合性

環境が存在しない、または同期が必要な場合は内部的に`blex sync`相当の処理を実行する。

ネットワークアクセスや大規模なcloneが必要になる場合も、宣言された状態へ同期してから起動する。

## 22.4 起動処理

1. 宣言状態と環境を同期する。
2. Primary Extension用overlayを生成する。
3. current platform向けruntime wheelを配置する。
4. 開発用manifestを生成する。
5. session設定を生成する。
6. `BLENDER_USER_*`を設定する。
7. Blenderを起動する。
8. bootstrap scriptを実行する。
9. Blender終了まで親CLIがプロセスを監督する。

## 22.5 source link

`auto`時の選択順:

```text
macOS/Linux:
  symlink
  copy

Windows:
  directory symlink
  junction
  copy
```

Primary Extensionはoverlayとして構成する。

```text
.bext/environment/extensions/blex_<project>_primary/
└── my_extension/
    ├── blender_manifest.toml
    ├── sourceへのリンク
    └── wheels/
```

---

# 23. debugpy

## 23.1 有効化

debugpyは`--debug`指定時のみ有効とする。

```bash
blex dev --debug
```

debugpyはPrimary Extensionのruntime dependencyへ追加しない。

`.bext/runtime`へ開発用依存として配置する。

## 23.2 接続設定

```bash
blex dev \
  --debug \
  --debug-host 127.0.0.1 \
  --debug-port 5678
```

## 23.3 クライアント待機

```bash
blex dev --debug --wait-for-client
```

`--wait-for-client`を`--debug`なしで指定した場合はエラーとする。

接続待機はBlender UIのmain threadをブロックしない方法で実行する。

デバッガ接続後、Blenderのmain thread上でPrimary Extensionを有効化する。

---

# 24. Reload Panel

## 24.2 実装方式

Blender起動時に実行されるbootstrap scriptが、operatorとpanelを直接登録する。

登録対象:

```text
BLEX_OT_reload_extension
BLEX_OT_restart_blender
BLEX_PT_development
```

# 26. エラー条件

以下をエラーとする。

- 対象Blenderが検出できない
- Blender Probeに失敗する
- Blender内部PythonとPython要件が不整合
- 対象Blender用の型スタブが存在しない
- 要求platform向けwheelが存在しない
- source manifestのplatform制約外をbuild対象に指定
- offline syncに必要なartifactが不足している
- staging環境のBlender検証に失敗する

---

# 27. 初期版の対象外

- sdistからのnative wheelクロスビルド
- target OS上でのruntime自動テスト
- Dockerまたは仮想マシンの自動起動
- Linux ARM64向けbuild
- Supplemental Extensionのソースディレクトリ参照
- Git repositoryからのSupplemental Extension導入
- Blender Extension Repositoryへのpublish
- private repositoryの認証情報管理
- 自動ファイル監視による即時reload
- 複数Blenderバージョンを同時対象とするbuild matrix
- clone元ユーザーリソースのスナップショット保持