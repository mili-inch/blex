# BLEX — Blender Extension開発CLI 仕様書

## 1. 概要

### 1.1 名称

CLIおよびPythonパッケージの名称を **BLEX** とする。

実行コマンドは以下とする。

```bash
blex
```

### 1.2 目的

BLEXは、Blender Extension開発に必要な以下の処理を統合するPython製CLIである。

- `uv`によるPythonプロジェクトの初期化
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

# 2. コマンド体系

初期版では以下のコマンドを提供する。

```text
blex create
blex init
blex sync

blex extension add
blex extension remove
blex extension list

blex build
blex dev
```

環境の作成、再作成、Supplemental Extensionの反映は、すべて`blex sync`へ統合する。

---

# 3. 宣言的な環境管理

## 3.1 原本

BLEXが管理する開発環境の原本は以下とする。

| ファイル                    | 役割                                         |
| ----------------------- | ------------------------------------------ |
| `pyproject.toml`        | プロジェクト、Blender環境、Supplemental Extensionの宣言 |
| `uv.lock`               | Python依存関係の解決結果                            |
| `blender_manifest.toml` | Primary Extensionのmanifest                 |

`.bext/environment`内の実体は原本として扱わない。

## 3.2 再構築方式

BLEXは再構築用のSeedディレクトリを作成・保持しない。

`blex sync`実行時は、その時点の以下の情報から開発環境を再生成する。

- `pyproject.toml`
- `uv.lock`
- `blex.lock`
- 対象Blenderの通常ユーザー環境

---

# 4. プロジェクト構造

```text
my-extension/
├── pyproject.toml
├── uv.lock
├── .python-version
├── README.md
├── .gitignore
├── src/
│   └── my-extension/
│       ├── blender_manifest.toml
│       ├── __init__.py
│       ├── operators.py
│       └── panels.py
├── dist/
└── .bext/
    ├── .gitignore
    └── environment/
        ├── config/
        ├── scripts/
        ├── datafiles/
        └── extensions/
```

`.bext/.gitignore`には以下を記入する。

```gitignore
*
```

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

[tool.blex.target]
blender-version = "4.5"
python-version = "3.11"
python-abi = "cp311"

[tool.blex.stubs]
provider = "fake-bpy-module"
blender-version = "4.5"

[tool.blex.environment]
config = "clean"
scripts = "clone"
datafiles = "clean"
extensions = "clone"

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

## 6.1 Blender実行ファイルの解決

以下の優先順位でBlender実行ファイルを解決する。

1. `--blender PATH`
2. `BLEX_BLENDER_EXECUTABLE`環境変数
3. `.bext/state.toml`に記録された実行ファイル
4. `PATH`
5. OS別の既定インストール候補

対象バージョンと互換性のあるBlenderが見つからない場合はエラーとする。

## 6.2 Blender Probe

Blenderをbackground起動し、以下を取得する。

- Blenderバージョン
- Blender Extension API情報
- Blender内部Pythonのバージョン
- Python ABI
- OS
- CPUアーキテクチャ
- CONFIGの通常ユーザーパス
- SCRIPTSの通常ユーザーパス
- DATAFILESの通常ユーザーパス
- EXTENSIONSの通常ユーザーパス

取得結果は診断およびキャッシュのため`.bext/state.toml`へ保存できる。

`.bext/state.toml`は環境構築の原本として使用しない。

## 6.3 非対話モード

以下のオプションをサポートする。

```text
--no-input
```

必須情報が不足している場合は推測せずエラーとする。

## 6.4 トランザクション

以下の処理はstaging directory内で実行し、成功後に確定する。

- `blex create`
- `blex init`
- `blex sync`
- `blex extension add`
- `blex extension remove`
- `blex extension update`
- `blex build`

既存環境との入れ替えはatomic operationを使用する。

---

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

対象Blenderに対応するパッケージが存在しない場合はエラーとする。

---

# 11. Blenderユーザー環境

## 11.1 対象リソース

以下を個別に管理する。

```text
CONFIG
SCRIPTS
DATAFILES
EXTENSIONS
```

## 11.2 モード

各リソースは以下のいずれかとする。

```text
clean
clone
```

```toml
[tool.blex.environment]
config = "clean"
scripts = "clone"
datafiles = "clean"
extensions = "clone"
```

ユーザーによる元ディレクトリの直接指定はサポートしない。

## 11.3 clean

`clean`は空のユーザーリソースとして作成する。

Blender本体に同梱されるsystem resourceは変更しない。

## 11.4 clone

`clone`は`blex sync`実行時点で、対象Blenderが通常使用しているユーザーリソースを検出してコピーする。

コピー元のスナップショットは保持しない。

次回の`blex sync`では、その時点の通常ユーザーリソースを改めて取得する。

## 11.5 環境変数

`blex dev`および環境検証時は、以下をプロジェクト専用環境へ設定する。

```text
BLENDER_USER_CONFIG
BLENDER_USER_SCRIPTS
BLENDER_USER_DATAFILES
BLENDER_USER_EXTENSIONS
```

---

# 12. CONFIGとEXTENSIONSの正規化

## 12.1 CONFIGがclone、EXTENSIONSがclean

- cloneされたCONFIG内のユーザーExtension repository設定を除去する。
- BLEX管理repositoryだけを登録する。

## 12.2 CONFIGがclean、EXTENSIONSがclone

- cloneされたEXTENSIONS領域を走査する。
- 有効なrepositoryをcleanなCONFIGへ登録する。
- 破損したrepositoryは登録しない。

## 12.3 CONFIGとEXTENSIONSがclone

- repositoryのパスを`.bext/environment/extensions`以下へ再マッピングする。
- 通常ユーザー環境への絶対パスを残さない。

## 12.4 Supplemental Extension

cloneされたEXTENSIONS環境をベースラインとし、その上に宣言済みSupplemental Extensionを追加する。

cloneされたExtensionは`blex.lock`では管理しない。

---

# 13. `blex sync`

## 13.1 構文

```bash
blex sync [OPTIONS]
```

オプション:

```text
--blender PATH
--offline
--refresh
--no-cache
--no-input
```

## 13.2 目的

`blex sync`は、現在の宣言とlockファイルからプロジェクト専用Blender環境を実体化する。

以下の両方を担当する。

- Blenderユーザー環境の作成・更新・修復
- Supplemental Extensionのインストール状態の同期

## 13.3 原本

同期時の原本は以下とする。

```text
pyproject.toml
uv.lock
blex.lock
blender_manifest.toml
```

現在の`.bext/environment`の状態は、同期内容を決定する原本として使用しない。

## 13.4 実行内容

1. `pyproject.toml`を検証する。
2. `uv.lock`との整合性を検証する。
3. Supplemental Extension宣言と`blex.lock`の整合性を検証する。
4. Blenderを解決してProbeする。
5. `.bext/staging/environment-*`を作成する。
6. CONFIGを`clean`または`clone`に従って生成する。
7. SCRIPTSを`clean`または`clone`に従って生成する。
8. DATAFILESを`clean`または`clone`に従って生成する。
9. EXTENSIONSを`clean`または`clone`に従って生成する。
10. CONFIGとEXTENSIONSのrepository設定を正規化する。
11. BLEX Primary repositoryを作成する。
12. BLEX Supplemental repositoryを作成する。
13. `blex.lock`のSupplemental Extensionを取得・検証する。
14. Supplemental Extensionをインストールする。
15. Extensionの有効化状態を反映する。
16. Blenderをbackground起動して環境を検証する。
17. 成功後に`.bext/environment`をatomicに置き換える。
18. `.bext/state.toml`を更新する。

## 13.5 `--refresh`

```bash
blex sync --refresh
```

以下のキャッシュを再取得する。

- Blender Extension Repository index
- Supplemental Extension ZIP
- wheel metadata

lockされたバージョン自体は変更しない。

バージョン更新は`blex extension update`で行う。

## 13.6 `--offline`

```bash
blex sync --offline
```

ネットワークへ接続せず、以下のみを使用する。

- `blex.lock`
- ローカルZIP
- `.bext/cache`
- `vendor/extensions`

必要なartifactが不足している場合はエラーとする。

## 13.7 破損環境

`.bext/environment`が存在しない、または破損している場合も、通常の`blex sync`で再作成する。

専用のrebuildコマンドは提供しない。

---

# 14. Supplemental Extension

## 14.1 定義

Supplemental Extensionは、Primary Extensionの開発・テストに必要な別のBlender Extensionである。

以下には含めない。

- Primary Extensionの配布ZIP
- Primary ExtensionのPython wheel
- Python runtime dependency

## 14.2 対応ソース

```text
Blender Extension Repository準拠URL
ローカルBlender Extension ZIP
```

ソースディレクトリおよびGit repositoryの直接指定は初期版ではサポートしない。

---

# 15. `blex extension add`

## 15.1 Repositoryから追加

```bash
blex extension add REPOSITORY_URL \
  --id EXTENSION_ID \
  [--version VERSION]
```

対話モードでは`--id`を省略できる。

その場合はrepositoryからExtensionを選択する。

`--version`を省略した場合は、対象Blenderと互換性がある最新バージョンを解決する。

宣言例:

```toml
[[tool.blex.supplemental-extensions]]
id = "dependency_extension"
version = ">=1.4,<2"
enabled = true
source = { repository = "https://extensions.example.com/" }
```

## 15.2 ZIPから追加

```bash
blex extension add ./vendor/extensions/helper.zip
```

ZIP内の`blender_manifest.toml`から以下を取得する。

- Extension ID
- バージョン
- Blender互換性
- platform互換性

プロジェクト外のZIPは既定では拒否する。

```bash
blex extension add /outside/helper.zip --vendor
```

`--vendor`指定時は以下へコピーする。

```text
vendor/extensions/<id>-<version>.zip
```

## 15.3 実行内容

1. sourceを解決する。
2. manifestを検証する。
3. archiveを取得または検査する。
4. `pyproject.toml`の宣言を更新する。
5. `blex.lock`を更新する。
6. 内部的に`blex sync`相当の処理を実行する。

既定では追加後すぐに環境へ反映する。

以下で同期を抑止できる。

```text
--no-sync
```

## 16.4 ID競合

以下とのID競合はエラーとする。

- Primary Extension
- 既存Supplemental Extension
- cloneされたベースラインExtension

cloneされたベースラインExtensionを置き換える場合:

```bash
blex extension add SOURCE --replace
```

宣言には以下を保存する。

```toml
replace = true
```

---

# 17. `blex extension remove`

## 17.1 構文

```bash
blex extension remove EXTENSION_ID
```

## 17.2 実行内容

- `pyproject.toml`から宣言を削除する。
- `blex.lock`から解決結果を削除する。
- 内部的に`blex sync`相当の処理を実行する。

以下で環境への即時反映を抑止できる。

```text
--no-sync
```

---

# 18. `blex extension update`

## 18.1 構文

```bash
blex extension update
blex extension update EXTENSION_ID
```

## 18.2 実行内容

Repository由来のSupplemental Extensionを再解決する。

- `pyproject.toml`のversion constraintを適用する。
- 新しい解決結果を`blex.lock`へ保存する。
- archiveのSHA-256を更新する。
- 内部的に`blex sync`相当の処理を実行する。

ローカルZIP由来のExtensionは自動更新しない。

---

# 19. `blex extension list`

## 19.1 構文

```bash
blex extension list
```

## 19.2 表示内容

```text
ID
source
requested version
resolved version
enabled
installed
baseline
replacement
lock status
```

---

# 20. Managed Extension Repository

プロジェクト専用EXTENSIONS領域に以下を構成する。

```text
.bext/environment/extensions/
├── cloned repositories
├── blex_<project>_primary/
└── blex_<project>_supplemental/
```

## 20.1 Primary repository

- 開発中Extension専用
- source overlayを保持する
- Reload Panelの操作対象とする
- build成果物には含めない

## 20.2 Supplemental repository

- `blex.lock`で管理されたExtension専用
- Repository URLまたはZIPからインストールする
- Reload Panelの操作対象外とする

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

## 24.1 既定動作

Reload Panelは既定で有効とする。

```bash
blex dev
```

無効化:

```bash
blex dev --without-reload-panel
```

## 24.2 実装方式

Blender起動時に実行されるbootstrap scriptが、operatorとpanelを直接登録する。

登録対象:

```text
BLEX_OT_reload_extension
BLEX_OT_restart_blender
BLEX_PT_development
```

これらは以下へ含めない。

- `blender_manifest.toml`
- Blender PreferencesのExtension一覧
- Primary Extensionの`register()`

## 24.3 Panel内容

```text
Extension Development
├── Primary Extension status
├── Supplemental Extension status
├── Wheel status
├── Debugger status
├── Reload Extension
└── Restart Blender
```

## 24.4 Reload処理

1. Primary Extensionを無効化する。
2. `unregister()`を実行する。
3. 対象namespace以下を`sys.modules`から削除する。
4. import cacheを無効化する。
5. source overlayを再同期する。
6. current platform向けwheelを検査する。
7. Primary Extensionを再importする。
8. Primary Extensionを有効化する。
9. 成否をPanelへ表示する。

Supplemental Extensionはリロード対象外とする。

native wheelが変更された場合はReloadを中止し、Restart Blenderを要求する。

## 24.5 Restart処理

1. session directoryへrestart requestを保存する。
2. Blenderを終了する。
3. 親`blex dev`プロセスがrequestを検出する。
4. overlayとwheelを再生成する。
5. 同じ宣言済み環境でBlenderを再起動する。

---

# 26. エラー条件

以下をエラーとする。

- 対象Blenderが検出できない
- Blender Probeに失敗する
- Blender内部PythonとPython要件が不整合
- 対象Blender用の型スタブが存在しない
- `uv.lock`が`pyproject.toml`と不整合
- `blex.lock`がSupplemental Extension宣言と不整合
- Supplemental Extension ZIPのmanifestが不正
- archiveのSHA-256がlockと異なる
- Primary ExtensionとSupplemental ExtensionのIDが競合
- cloneされたExtensionとの未解決ID競合
- 要求platform向けwheelが存在しない
- source manifestのplatform制約外をbuild対象に指定
- `--wait-for-client`を`--debug`なしで指定
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

---

# 28. コマンド動作一覧

| コマンド                              | 期待される結果                                |
| --------------------------------- | -------------------------------------- |
| `blex create`                     | 新規Extension、uv環境、型スタブ、宣言済みBlender環境を生成 |
| `blex init`                       | 既存プロジェクトを非破壊でBLEXプロジェクト化               |
| `blex sync`                       | 現在の宣言とlockからBlender環境全体を同期・修復          |
| `blex extension add`              | Supplemental Extensionを宣言・lockし、環境へ反映  |
| `blex extension remove`           | Supplemental Extensionを宣言・lock・環境から削除  |
| `blex extension update`           | Repositoryを再解決し、lockと環境を更新             |
| `blex extension list`             | 宣言・解決・インストール状態を表示                      |
| `blex build`                      | current platform向けwheel同梱ZIPを生成        |
| `blex build --all-platforms`      | 対応wheelが存在する全target向けZIPを生成            |
| `blex build --without-wheels`     | wheelを含まないZIPを生成                       |
| `blex dev`                        | 宣言済み環境を同期し、Reload Panel付きでBlenderを起動   |
| `blex dev --debug`                | Blender内部でdebugpy serverを起動            |
| `blex dev --without-reload-panel` | Reload用operatorおよびpanelを登録せず起動         |
