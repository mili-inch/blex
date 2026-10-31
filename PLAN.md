# Blender Extension Development CLI `blex` 仕様書

## 1. 概要

`blex`は、単一のBlender Extensionを開発するPythonプロジェクトの初期化、開発用Blenderの起動、および配布前stagingの作成を行うCLIである。

CLI名とPython distribution名はともに`blex`とする。初期リリースのバージョンは`0.1.0`とする。

本仕様では次の3コマンドを提供する。

- `blex init`: Extensionプロジェクトを初期化する。
- `blex dev`: 隔離したユーザー領域へExtensionを配置し、開発用Blenderを起動する。
- `blex stage`: 実行中のPython・OS・architecture向けWheelを収集し、stagingを作成する。

本仕様に明記していない後方互換、オプションの別名、設定の別名、自動探索、silent fallbackは実装しない。安全に継続できない入力や状態は明示的なエラーとして処理する。

## 2. 対応範囲

### 2.1 blexの実行環境

- blex自身の`requires-python`は`>=3.11`とする。
- 対応host processはWindows x64、Windows ARM64、macOS x64、macOS ARM64、Linux x64とする。
- Linux ARM64およびその他のOS・architectureは対応hostに含めない。
- blexはPyPIからinstallできるdistributionとして公開する。
- blex内部の通常処理は標準ライブラリを使用する。TOMLの読み書きに限り`tomlkit`を使用する。
- 開発依存関係はこの外部依存制限に含めない。
- Blenderとpipは外部プロセスとして実行する。自動Wheel収集にはblexと同じPython環境にpipを必要とし、`sys.executable -m pip`で実行する。
- debugpyはネットワーク取得せず、blexに同梱したWheelを利用する。

### 2.2 Blender

- 対象Blenderは4.2.0以上とし、上限は設けない。
- 新規Manifestの`blender_version_min`は`4.2.0`とする。
- blexは`init --blender`および`dev`でBlenderのバージョンを明示検査しない。
- 古いBlenderやManifestの対応範囲外のBlenderを使用した結果は、Blender自身の終了またはExtension有効化失敗として扱う。
- `blender_version_max`が既存Manifestに存在しても、blex自身では実行中Blenderとの比較を行わない。

### 2.3 Wheel対象Platform

サポートするPlatform識別子は次の5種類だけとする。

1. `windows-x64`
2. `windows-arm64`
3. `macos-x64`
4. `macos-arm64`
5. `linux-x64`

`linux-arm64`および上記以外の識別子はサポートしない。

### 2.4 非対応範囲

- 1プロジェクト内の複数Extensionを同時に管理しない。
- project rootやManifestの上位ディレクトリ探索を行わない。
- source-dir内を再帰検索してManifestを自動発見しない。
- `dev`および`stage`ではBlender実行ファイルからPythonバージョンを検出しない。
- `stage`ではBlender標準のExtension buildコマンドを実行しない。
- `stage`ではZIPなどの最終配布物を作成しない。
- v0.1では安定した公開Python APIを提供しない。
- shell completion、短縮オプション、オプション名の別名は提供しない。

## 3. blex自身のパッケージ

### 3.1 パッケージメタデータ

blex自身の`pyproject.toml`は次を基準とする。

```toml
[build-system]
requires = ["hatchling>=1.26,<2"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/blex"]

[tool.hatch.build]
artifacts = ["src/blex/vendor/*.whl"]

[project]
name = "blex"
version = "0.1.0"
description = "Blender Extension development CLI"
readme = "README.md"
requires-python = ">=3.11"
license = "GPL-3.0-or-later"
dependencies = [
    "tomlkit>=0.15,<0.16",
]

[project.scripts]
blex = "blex.main:main"

[dependency-groups]
dev = [
    "build>=1.3,<2",
    "hatchling>=1.26,<2",
    "pip>=25.1,<27",
    "pytest>=9.1,<10",
    "pytest-cov>=7.1,<8",
    "ruff>=0.16,<0.17",
    "pyrefly==1.1.1",
]
```

- authorsおよびproject URLsは設定しない。
- リポジトリルートにGPLv3全文の`LICENSE`を配置する。
- 開発用の`uv.lock`をコミットする。uvはcontributor環境の管理にのみ使用でき、runtime・build backend・CIの必須依存にはしない。
- build frontendはPyPA `build`とし、`python -m build`でsdistとWheelを作成する。
- sdistとWheelにはtemplates、同梱Wheel、licenseを含める。sdistからも同じ資源を含むWheelをbuildできる。
- distribution versionは`importlib.metadata.version("blex")`から取得する。

### 3.2 パッケージ構成

```text
src/blex/
├── __init__.py
├── __main__.py
├── main.py
├── cli.py
├── commands/
│   ├── __init__.py
│   ├── init.py
│   ├── dev.py
│   └── stage.py
├── core/
│   ├── __init__.py
│   ├── project.py
│   ├── manifest.py
│   ├── config.py
│   ├── environment.py
│   ├── filesystem.py
│   ├── wheels.py
│   ├── cache.py
│   └── errors.py
├── adapters/
│   ├── __init__.py
│   ├── blender.py
│   └── pip.py
├── templates/
│   ├── project/
│   │   ├── pyproject.toml
│   │   ├── python-version
│   │   ├── README.md
│   │   ├── LICENSE
│   │   └── gitignore
│   ├── extension/
│   │   ├── blender_manifest.toml
│   │   ├── __init__.py
│   │   ├── operators.py
│   │   └── panels.py
│   └── bootstrap/
│       └── dev.py
└── vendor/
    ├── debugpy-1.8.21-py2.py3-none-any.whl
    └── licenses/
        └── debugpy/
            └── LICENSE
```

- `blex.main:main`は`main(argv: Sequence[str] | None = None) -> int`としてテスト可能にする。
- console scriptは`main`の戻り値を終了コードとして使用する。
- `python -m blex`は同じ`main`を呼び、`SystemExit`で終了する。
- template、同梱Wheel、同梱ライセンスは`importlib.resources`で参照する。
- `tomlkit`以外の`packaging`、`filelock`、`platformdirs`などをruntime依存関係へ追加しない。

### 3.3 テキストファイル

blexが新規生成するテキストファイルは、OSを問わず次の形式とする。

- UTF-8
- BOMなし
- LF改行
- ファイル末尾に改行あり

既存TOMLを変更する場合は`tomlkit`を用い、変更対象外の既存値、コメント、キー順、未知のテーブルを保持する。ただし構文不正や仕様上の型不正を修復せず、エラーにする。

## 4. 共通プロジェクトモデル

### 4.1 project root

- 各コマンドの位置引数`DIRECTORY`をproject rootとする。
- `DIRECTORY`を省略した場合は現在の作業ディレクトリをproject rootとする。
- 相対`DIRECTORY`は現在の作業ディレクトリ基準で解決する。
- 上位ディレクトリから`pyproject.toml`を探索しない。
- `init`は存在しない`DIRECTORY`と、その不足している親ディレクトリを作成できる。
- `dev`と`stage`ではproject root、`pyproject.toml`、解決済みsource-dir、`blender_manifest.toml`が存在しなければエラーにする。

### 4.2 単一Extensionとsource-dir

- blexが1回のコマンドで扱うManifestは、解決済みsource-dir直下の`blender_manifest.toml`だけとする。
- source-dirの優先順位は、CLI指定、`[tool.blex].source-dir`、組み込み既定値の順とする。
- `init`の組み込み既定値は`src/<生成したmanifest-id>`とする。
- `dev`および`stage`で設定がない場合は、`[project].name`を命名規則でmanifest IDへ変換し、`src/<manifest-id>`とする。
- source-dirを再帰検索したり、他のManifestへ自動で切り替えたりしない。
- source-dirは実体を解決した後もproject rootの子孫でなければならない。project rootそのものはsource-dirにできない。
- source-dirはproject root直下の`.blex`または`.git`と同一、またはその子孫にできない。
- source-dir内のシンボリックリンクは、リンク切れ、循環、またはsource-dir外を指す場合にエラーにする。
- Windowsのjunction、mount point、その他reparse pointはpath安全性検査ではsymlinkと同様に扱う。

### 4.3 名前の生成

新しいmetadataを生成する`init`では、`--name`があればその値、なければproject rootの最終ディレクトリ名を基準入力とする。`--force`で既存metadataをmergeする場合の値の選択は第8.3節に従う。

基準入力にはASCII英字、ASCII数字、空白、`-`、`_`だけを許可する。前後の空白を除去した結果が空ならエラーにする。

project名は次の手順で生成する。

1. ASCII小文字へ変換する。
2. 空白と`_`を`-`へ変換する。
3. 連続する`-`を1個へまとめる。
4. 前後の`-`を除去する。
5. `^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$`に一致しなければエラーにする。

Manifest IDは次の手順で生成する。

1. ASCII小文字へ変換する。
2. 空白と`-`を`_`へ変換する。
3. 連続する`_`を1個へまとめる。
4. 前後の`_`を除去する。
5. `^[a-z][a-z0-9_]*$`に一致しなければエラーにする。
6. Pythonのkeywordに一致すればエラーにする。

表示名は基準入力を空白、`-`、`_`で単語分割し、各単語を小文字化して先頭だけ大文字にし、単一の空白で連結する。

`--name`はproject名だけでなく、Manifest ID、Manifest表示名、生成クラス名、既定source-dirを導く基準入力全体を上書きする。

### 4.4 Pythonバージョン表記

`init --python`およびinitが生成・検証する`.python-version`のPythonバージョンは`<major>.<minor>`とする。

- majorは1以上の整数とする。
- minorは0以上の整数とする。
- 各要素の不要な先頭ゼロを禁止する。
- 前後の空白は除去する。
- `3`、`3.11.2`、`03.11`、複数行はエラーにする。
- blexが扱う対象Pythonバージョン自体には下限を設けない。
- `init --python`の反復指定はエラーにする。
- `.python-version`は環境構築ツール・CIが使用するための情報であり、`dev`と`stage`は読み取らない。
- `dev`と`stage`は実行中のPythonを使用する。stageの出力pathはPythonバージョンに依存しない。

### 4.5 Pathの解決と安全性

- `DIRECTORY`以外の相対CLI pathおよびTOML pathはproject root基準で解決する。
- 設定へ生成するproject内pathは`/`区切りの相対pathとする。
- Blenderの値は、絶対pathであるか`/`または`\`を含む場合はpathとしてproject root基準で解決し、それ以外はcommand名として`PATH`から`shutil.which`で解決する。
- Windowsのdrive-relative形式`C:program.exe`は受理せず、絶対pathまたは明示的な相対pathを要求する。
- macOSの`.app` bundle自体はBlender実行ファイルとして受理しない。bundle内部の実行バイナリを明示する。
- source-dir、output-dir、blexが管理する削除・置換対象は実体pathを検証する。
- Blex管理ディレクトリ自体がシンボリックリンクの場合は追跡せずエラーにする。
- 読み取り元として明示されたBlendファイル、Blender実行ファイル、user-resourcesはproject外に置ける。

## 5. 設定

### 5.1 設定場所

設定はproject rootの`pyproject.toml`内だけに記述する。blex独自の環境変数設定は提供しない。

`init`で生成する基本形は次のとおりとする。`source-dir`は実際に生成した値へ置き換える。

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

`[tool.blex].blender`は共有位置に置くが、`stage`では読み取っても解決・実行しない。

### 5.2 許可するキー

`[tool.blex]`:

- `source-dir`: path文字列
- `blender`: 実行ファイルpathまたはcommand名

`[tool.blex.dev]`:

- `link-mode`: `auto`、`symlink`、`copy`のいずれか
- `reload-panel`: boolean
- `wheels`: boolean
- `debugpy`: boolean
- `debugpy-host`: 空でない文字列
- `debugpy-port`: 1以上65535以下の整数
- `blend-file`: path文字列
- `user-resources`: path文字列

`[tool.blex.stage]`:

- `output-dir`: path文字列
- `wheels`: boolean

`[tool.blex]`およびその`dev`、`stage`で未知のキーを検出した場合はエラーにする。値の型が異なる場合、空文字列の場合、配列が空の場合もエラーにする。他の`tool` tableやproject metadataの未知の値は保持する。

### 5.3 優先順位

値の優先順位は次のとおりとする。

1. 明示されたCLI option
2. `pyproject.toml`のblex設定
3. 組み込み既定値

- CLIのboolean optionは未指定、true、falseを区別する。
- 環境変数でこの優先順位を上書きしない。
- pipにはindex等の標準設定ファイルと`PIP_*`環境変数を継承する。対象環境や推移依存の解決を変更するpip設定は使用しない。

### 5.4 無効機能に付随する値

- 設定ファイル内の`debugpy-host`と`debugpy-port`は、`debugpy = false`でも保持でき、型と範囲は検証する。
- CLIで`--debugpy-host`または`--debugpy-port`を明示したにもかかわらず、最終的なdebugpyが無効ならエラーにする。
- 最終的なwheelsが無効なとき、`--refresh-wheels`の明示指定はエラーにする。
- `dev --no-wheels`ではpipを実行しない。
- `stage --no-wheels`でも現在のPlatformを生成Manifestに使用するが、pipは実行しない。

## 6. 共通CLI規約

### 6.1 入口

```text
blex [--version] [--verbose] <command>
```

- parserは標準ライブラリ`argparse`を使用する。
- `allow_abbrev=False`とする。
- subcommandは必須とする。
- `--verbose`はsubcommandより前だけで受理する。
- `--version`は`blex 0.1.0`形式を出力する。
- 標準の`--help`を提供する。
- 短縮option、option alias、shell completion、対話prompt、色付き出力、quiet modeは提供しない。

### 6.2 出力

- 通常の進捗、成功情報、cache hitはstdoutへ出力する。
- 警告とエラーはstderrへ出力する。
- pip downloadのstdoutとstderrは通常時も親端末へそのまま流す。
- Blenderのstdoutとstderrも親端末へそのまま流す。
- `--verbose`では解決済みpath、実行command、cache判定、生成先を追加表示する。
- `PIP_*`の値や認証情報は表示しない。

### 6.3 終了コード

- 正常終了、help、versionは0とする。
- argparseによる引数解析エラーは2とする。
- 設定、validation、TOML、pip、Wheel cache、file operationなどの処理済みエラーは1とする。
- `dev`でBlenderが通常終了した場合はBlenderの終了コードをそのまま返す。
- Ctrl+Cによる終了は130とする。
- 予期した処理エラーだけをblex固有例外として捕捉し、`error: <message>`形式で表示する。
- 予期しない例外は捕捉せず、Python tracebackを表示する。
- Blender-Python検出cacheのJSON破損や内部schema破損は特別に捕捉・修復せず、予期しない例外として失敗させる。

## 7. projectとManifestのvalidation

### 7.1 pyproject.toml

`dev`および`stage`では次を検証する。

- TOMLとして読み取れる。
- `[project]`がtableである。
- `[project].name`が空でない文字列である。
- `[project].dependencies`は、省略されているか、文字列だけからなる配列である。
- blex設定が第5章のschemaに一致する。

`[project].dependencies`が省略されている場合は、依存関係なしとして扱い、原本へ追記しない。

### 7.2 blender_manifest.toml

blexはBlenderの完全なManifest validatorを再実装しない。`dev`および`stage`では次だけを検証する。

- TOMLとして読み取れる。
- `schema_version`が文字列`1.0.0`である。
- `id`が第4.3節のManifest ID規則に一致し、Python keywordではない。
- `type`が文字列`add-on`である。
- `platforms`が存在する場合、対応Platform識別子だけからなる非空配列である。
- `wheels`が存在する場合、空でない文字列だけからなる非空配列である。
- blexが読み書きする値の型が正しい。

version、name、tagline、maintainer、license、Blender versionなどの詳細なBlender規則はblexで重複検証しない。未知のManifest fieldとtableは保持する。

## 8. `blex init`

### 8.1 CLI

```text
blex init [DIRECTORY]
    [--force]
    [--name NAME]
    [--description DESCRIPTION]
    [--python X.Y]
    [--blender EXECUTABLE]
    [--source-dir PATH]
```

`--description`の組み込み既定値は`Add your description here`とする。`--description`は`[project].description`と新規READMEだけへ反映し、Manifestのtaglineへは反映しない。

descriptionは前後の空白を除去した後に空であってはならず、NUL、CR、LFを含まない1行の文字列とする。

### 8.2 既存ディレクトリとproject marker

project markerは次のいずれかとする。

- project root直下の`pyproject.toml`
- 解決済みsource-dir直下の`blender_manifest.toml`

対象ディレクトリが存在すること自体はエラーにしない。

- project markerが存在し、`--force`がなければ、変更せずエラーにする。
- project markerが存在し、`--force`があれば、第8.3節のmergeを行う。
- project markerが存在しない既存ディレクトリでは、`--force`なしでも不足ファイルを作成する。
- 他の場所のManifestを探索してproject marker扱いしない。
- 対象`DIRECTORY`が通常ファイルまたはシンボリックリンクならエラーにする。

### 8.3 `--force`と既存ファイルのmerge

initが既存ファイルを扱う規則は次のとおりとする。

- `--name`がなく既存`[project].name`があれば、その値をproject名として保持し、既定source-dir用のManifest IDをその値から導く。
- 解決済みsource-dirに既存Manifestがあれば、`--name`がない限り、そのManifestの`id`と`name`をExtension生成物の基準として保持する。
- 既存project名と既存Manifest IDは相互に同期せず、それぞれの既存値を保持する。
- 既存`[project].name`も既存Manifestもない場合だけ、第4.3節の基準入力から両方を生成する。
- 既存TOMLには不足しているfield、table、list entryだけを追加する。
- 既存値、コメント、key順、未知のfieldは変更しない。
- 既存`requires-python`は変更しない。fieldがなければ`>=3.11`を追加する。
- 既存`.gitignore`には不足している正規patternだけを末尾へ追記する。
- 既存README、`LICENSE`、`__init__.py`、`operators.py`、`panels.py`は内容を変更しない。
- 新規Manifest用の長いcomment templateを既存Manifestへ追加しない。
- 既存TOMLが構文不正または期待型と異なる場合は修復せずエラーにする。
- 明示した`--name`が既存project名、Manifest ID、Manifest表示名と矛盾する場合はエラーにする。
- 明示した`--description`が既存`[project].description`と矛盾する場合はエラーにする。
- 明示した`--source-dir`が既存`[tool.blex].source-dir`と矛盾する場合はエラーにする。
- `.python-version`だけは、第8.4節の明示指定によって上書きできる。

このmerge規則は、project markerがない既存ディレクトリ内で生成対象fileと衝突した場合にも適用する。

### 8.4 Pythonバージョン

新規projectの`[project].requires-python`は常に`>=3.11`とする。`--python`や`--blender`はこの値を変更しない。

`.python-version`の決定順は次のとおりとする。

1. `--python X.Y`
2. `--blender EXECUTABLE`から検出したPython
3. 既存`.python-version`
4. `3.11`

- `--python`と`--blender`を同時指定した場合は`--python`を使用し、Blenderを起動せず、Blender cacheにも触れない。
- `--python`は指定値をそのまま正規化して`.python-version`へ書く。
- `--blender`はPython検出だけに使用し、生成または既存の`[tool.blex].blender`を追加値で上書きしない。
- 明示指定がない既存`.python-version`は保持するが、形式不正ならエラーにする。
- Pythonバージョンが3.11未満でも、`requires-python`を変更したり警告で代替したりしない。

### 8.5 `init --blender`によるPython検出

Blender実行ファイルは第4.5節の規則で解決する。

cache miss時は次の形で実行する。

```text
<blender> --background --factory-startup --python-expr <expression>
```

- expressionは`sys.version_info`からmajorとminorだけを取得し、blex固有prefix付きの1行としてstdoutへ出力する。
- blexは固有prefixの行だけを検出結果として解釈する。
- timeoutは30秒とする。
- 非0終了、timeout、prefix欠落、不正なバージョンはinit失敗とする。
- 通常時は検出用Blender出力を表示しない。
- 失敗時は取得できたstdoutとstderrをエラー情報へ含める。
- `--verbose`では実行commandとcache判定を表示する。
- Blender自体のversionは取得・検証しない。

cacheはproject単位で`.blex/cache/blender-python.json`へ保存する。実行ファイル全体のSHA-256をkeyとし、pathではなくcontent hashでhit判定する。

JSONの論理形式は次とする。

```json
{
  "format": 1,
  "entries": {
    "<sha256>": {
      "python": "3.11",
      "executable": "C:/resolved/path/to/blender"
    }
  }
}
```

- hash hit時はBlenderを起動しない。
- executable pathは診断metadataであり、keyではない。
- cache更新は一時fileから原子的に置換する。
- cache fileおよび`.blex`は必要になった時だけ作成する。
- cache破損は捕捉、repair、fallbackしない。

### 8.6 transaction

initは書き込み前に入力、TOML、path、衝突、生成内容をすべて検証する。

- 各fileは同じdirectory内の一時fileへ書き、置換する。
- 複数fileの処理中に捕捉可能な失敗が起きた場合、変更した既存fileを元のbyte列へ戻し、新規作成fileを削除する。
- blexが新規作成した空directoryだけをrollback時に削除する。
- userが元から所有していたfileやdirectoryをrollbackで削除しない。
- initはvenv作成、`uv sync`、pip、network accessを行わない。
- `--blender`による明示的な検出だけが外部process実行の例外である。

### 8.7 新規生成物

既定構成は次のとおりとする。`dist`と`.blex`はinitだけでは作成しない。ただし`init --blender`でcacheが必要な場合は`.blex`を作成する。

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

`.blex`をblexが初めて作成・利用するときは`.blex/.gitignore`も作成する。

project rootの`.gitignore`:

```gitignore
.venv/
.blex/*
!.blex/.gitignore
dist/
__pycache__/
*.py[cod]
```

`.blex/.gitignore`:

```gitignore
*
!.gitignore
```

既存`.blex/.gitignore`がある場合は内容を置き換えず、不足する上記2行だけを末尾へ追加する。

### 8.8 新規`pyproject.toml`

```toml
[project]
name = "my-extension"
version = "0.1.0"
description = "Add your description here"
readme = "README.md"
authors = [
    { name = "Your Name", email = "you@example.com" },
]
requires-python = ">=3.11"
license = "GPL-3.0-or-later"
dependencies = []

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

- 新規projectには`build-system`を追加しない。
- project versionは`0.1.0`、Manifest versionは`1.0.0`であり、両者を同期しない。
- GPLv3全文の`LICENSE`をproject rootへ生成する。
- `uv.lock`は生成しない。

### 8.9 新規`blender_manifest.toml`

新規Manifestは、値を置換した次のtemplateを完全なcomment群とともに生成する。

```toml
schema_version = "1.0.0"

id = "my_extension"
version = "1.0.0"
name = "My Extension"
tagline = "This is another extension"
maintainer = "Developer name"
type = "add-on"

# # Optional: link to documentation, support, source files, etc.
# website = "https://extensions.blender.org/add-ons/my-extension/"

# # Optional: tag list defined by Blender and server, see:
# # https://docs.blender.org/manual/en/latest/advanced/extensions/tags.html
# tags = ["Animation", "Sequencer"]

blender_version_min = "4.2.0"
# # Optional: Blender version that the extension does not support.
# # Earlier versions are supported.
# # This can be omitted and defined later on the extensions platform
# # if an issue is found.
# blender_version_max = "5.1.0"

# License conforming to https://spdx.org/licenses/ (use the "SPDX:" prefix).
# https://docs.blender.org/manual/en/latest/advanced/extensions/licenses.html
license = [
    "SPDX:GPL-3.0-or-later",
]

# # Optional: required by some licenses.
# copyright = [
#     "2002-2024 Developer Name",
#     "1998 Company Name",
# ]

# # Optional: list of supported platforms.
# # If omitted, the extension will be available in all operating systems.
# platforms = ["windows-x64", "macos-arm64", "linux-x64"]
# # Other supported platforms: "windows-arm64", "macos-x64"

# # Optional: bundle 3rd party Python modules.
# # https://docs.blender.org/manual/en/latest/advanced/extensions/python_wheels.html
# wheels = [
#     "./wheels/hexdump-3.3-py3-none-any.whl",
#     "./wheels/jsmin-3.0.1-py3-none-any.whl",
# ]

# # Optional: add-ons can list which resources they will require:
# # * files (for access of any filesystem operations)
# # * network (for internet access)
# # * clipboard (to read or write the system clipboard)
# # * camera (to capture photos and videos)
# # * microphone (to capture audio)
# #
# # If using network, remember to also check bpy.app.online_access.
# # https://docs.blender.org/manual/en/latest/advanced/extensions/addons.html#internet-access
# #
# # For each permission it is important to also specify the reason why it
# # is required. Keep this a single short sentence without a period at
# # the end. For longer explanations use the documentation or detail page.
#
# [permissions]
# network = "Need to sync motion-capture data to server"
# files = "Import/export FBX from/to disk"
# clipboard = "Copy and paste bone transforms"

# # Optional: advanced build settings.
# # https://docs.blender.org/manual/en/latest/advanced/command_line/extension_arguments.html
# [build]
# # These are the default build excluded patterns.
# # You only need to edit them if you want different options.
# paths_exclude_pattern = [
#     "__pycache__/",
#     "/.git/",
#     "/*.zip",
# ]
```

`platforms`、`wheels`、`blender_version_max`、website、tags、permissions、buildはcommentのままであり、active fieldとしては生成しない。

### 8.10 最小Extension sample

`operators.py`はmanifest IDから生成したclass prefixを使用し、次の意味を持つ。

```python
import bpy


class MY_EXTENSION_OT_hello(bpy.types.Operator):
    bl_idname = "my_extension.hello"
    bl_label = "Say Hello"

    def execute(self, _context):
        self.report({"INFO"}, "Hello from My Extension!")
        return {"FINISHED"}
```

`panels.py`:

```python
import bpy


class MY_EXTENSION_PT_hello(bpy.types.Panel):
    bl_idname = "MY_EXTENSION_PT_hello"
    bl_label = "My Extension"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "My Extension"

    def draw(self, _context):
        self.layout.operator("my_extension.hello", icon="INFO")
```

`__init__.py`:

```python
import bpy

from .operators import MY_EXTENSION_OT_hello
from .panels import MY_EXTENSION_PT_hello

_CLASSES = (
    MY_EXTENSION_OT_hello,
    MY_EXTENSION_PT_hello,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
```

- `MY_EXTENSION`、`my_extension`、`My Extension`、report messageは生成名で置換する。
- `bl_info`は生成しない。
- READMEは表示名のH1、description、`Development`見出し、`blex dev`のcode blockだけを含む。

新規READMEの正確な形は次とする。

````markdown
# My Extension

Add your description here

## Development

```console
blex dev
```
````

## 9. Wheel処理

Wheel処理は`dev`と`stage`で共有する。

### 9.1 自動収集と手書きWheel

- 自動収集は既定で有効とする。
- `--no-wheels`または設定で無効化できる。
- 自動収集の入力は`[project].dependencies`だけとする。
- optional dependenciesおよびdependency groupsは自動収集しない。
- 自動収集を無効にしても、元Manifestの手書き`wheels` entryと参照fileは検証・配置する。
- 自動収集を無効にしても、最終Extension内の`wheels/` directoryは作成する。
- 自動Wheelも手書きWheelも0件なら、生成Manifestから`wheels` fieldを省略する。
- 自動収集が有効でも`project.dependencies`が空ならpipを実行しない。

### 9.2 dependencyの許可範囲

自動Wheel収集が有効な場合、`[project].dependencies`にはPEP 508のindexから解決できるrequirementとextras、markerを許可する。自動収集が無効な場合は配列と文字列の型だけを第7.1節で検証し、以下のrequirement形式検査を行わない。

次はサポートせず、pip実行前にエラーにする。

- direct URL requirement
- VCS requirement
- local fileまたはlocal directory
- editable install
- requirements file
- constraints file
- pip独自option

blexは依存関係resolverを実装せず、pipの公開CLIへ委ねる。requirement文字列にNUL、CR、LFを含めてはならない。

### 9.3 対象Python

`dev`と`stage`はblexを実行しているPythonだけを対象にする。
`sys.executable`をpipの実行に使い、実行環境のPythonバージョンをcacheの識別に使う。
Python指定optionや設定、`.python-version`の読み取り、別Pythonの探索・取得は行わない。
Blender実行ファイルからのPython検出も行わない。
利用者は対象Blenderに対応するPython環境でblexを実行する。

### 9.4 対象Platform

`dev`と`stage`はblex processの現在のPlatformだけを対象にする。
現在Platformは`platform.system()`と`platform.machine()`から次のように決定する。

- Windows + AMD64/x86_64: `windows-x64`
- Windows + ARM64/aarch64: `windows-arm64`
- macOS + x86_64: `macos-x64`
- macOS + arm64/aarch64: `macos-arm64`
- Linux + x86_64/AMD64: `linux-x64`
- その他: エラー

Rosettaやemulation環境ではhardware自体ではなくblex processから観測されるarchitectureを使用する。別architectureへ自動で切り替えない。

### 9.5 対象環境とWheel互換タグ

直接・推移的依存のmarker、extras、Python/ABI/OS互換タグはpipが現在の環境で評価する。
blex自身の互換タグ一覧やmacOS/glibcの固定baselineは設けない。
配布先のBlenderのPython、architecture、OSの最低要件を満たす環境でstageを実行する。
複数環境向けの生成はCI matrix等で環境ごとに独立して実行する。

### 9.6 Wheel取得

- 自動収集が必要な場合だけ`sys.executable -m pip --version`を取得する。
- pipが同じPython環境に存在しなければ、導入先を示すエラーにする。別の実行ファイルを探索しない。
- shellを介さず`sys.executable -m pip download`を実行する。
- `--only-binary=:all: --dest <temporary-directory> --disable-pip-version-check --no-input --no-cache-dir`とdependency文字列を順序どおり渡す。
- pipが推移依存も含めて解決し、現在環境に対応するWheelを取得する。
- 標準出力・標準エラーを継承する。認証情報をblex自身のlogへ出さない。
- 実行不能、非zero終了、Wheel以外の取得物はエラーにする。
- sdistのbuild、別resolverへの切替は行わない。

### 9.7 環境の分離

1回のstageは現在のPython・Platformのsource treeだけを作成する。
生成Manifestのplatformsはそのtreeの1 Platformだけとする。
出力先は常に`<output-dir>/source/`とする。複数環境の成果物を共存させる場合は、別の作業領域または`--output-dir`を明示して分ける。

### 9.8 手書きWheel path

元Manifestの`wheels` entryはManifest directory基準とする。

- 相対pathだけを許可する。
- path separatorは`/`だけを許可し、`\`を禁止する。
- 先頭の`./`は許可する。先頭以外の`.` segmentは禁止する。
- `..`を含むpath、絶対path、空pathを禁止する。
- suffixが`.whl`でなければエラーにする。
- 実体がsource-dir内に収まり、通常fileとして存在しなければエラーにする。
- Manifestへ書くpath separatorは`/`とする。
- 自動Wheelは`./wheels/<filename>`へ配置する。
- 手書きpathは元の相対位置を維持する。
- 自動Wheelと手書きWheelが同じ相対pathなら、同一SHA-256の場合だけdeduplicateし、異なればエラーにする。
- source-dir内のWheel symlinkにも第4.2節のsymlink制約を適用する。

### 9.9 Wheel cache

cache layoutは次とする。

```text
.blex/wheels/
└── <fingerprint>/
    ├── metadata.json
    └── *.whl
```

fingerprintは、解決方式format 1、順序を保持したdependency文字列、Python、Platform、Python実行path、pip version、runtime情報、全`PIP_*`環境変数の名前と値からcanonical JSONを作り、そのSHA-256とする。runtime情報にはPEP 508の全marker値、Python build、sysconfig Platform、SOABI、libc、macOS versionを含める。

- `PIP_*`の値はfingerprint計算にだけ使用し、`metadata.json`やlogへ保存しない。
- pip設定fileの内容はfingerprintへ含めない。pip設定fileだけを変更した場合は`--refresh-wheels`を必要とする。
- 環境の区別はfingerprintに含め、cache pathとlock pathにPython・Platformのdirectory階層を設けない。
- 同じfingerprintは期限なしで再利用する。
- index上で未固定dependencyの新versionが公開されてもfingerprintは変化しないため、自動更新しない。更新には`--refresh-wheels`を使用する。
- `--refresh-wheels`は対象fingerprintを再取得し、成功後に原子的に置き換える。
- cache entryには取得Wheelのfilename、SHA-256、Python、Platform、Python実行path、pip version、runtime情報、dependency文字列を記録する。
- metadata欠落、不正JSON、file欠落、hash不一致、予期しないfileはcache破損として明示的なエラーにする。
- cache破損時に通常実行でrepairしない。`--refresh-wheels`だけが置換を許可する。
- cache更新は一時directoryで完了させてからrenameする。
- `.blex/locks/wheels/<fingerprint>.lock`でfingerprint単位にOS file lockを取得し、待機せず競合エラーにする。
- Windowsでは`msvcrt`、POSIXでは`fcntl`を使用し、外部lock libraryを追加しない。

`metadata.json`の論理形式は次とする。dependency配列は入力順を保持し、Wheel配列はfilename順とする。

```json
{
  "format": 1,
  "fingerprint": "<sha256>",
  "python": "3.11",
  "platform": "windows-x64",
  "pip": {
    "python_executable": "C:/environment/Scripts/python.exe",
    "version": "<python -m pip --version output>"
  },
  "runtime": {"<runtime field>": "<value>"},
  "dependencies": ["example>=1"],
  "wheels": [
    {
      "filename": "example-1.0-py3-none-any.whl",
      "sha256": "<sha256>"
    }
  ]
}
```

### 9.10 不変Wheel object

cache entryのlockを保持したまま、検証済みWheelを
`.blex/wheel-objects/<sha256>/<filename>`へ原子的にcopyする。
objectごとに`.blex/locks/wheel-objects/<sha256>.lock`を取得する。
既存objectは通常fileでありhashが一致するときだけ再利用し、破損・linkはエラーにする。
objectはrefreshでも置換・削除せず、devとstageはこの不変pathを参照する。
これにより取得から配置までの間のrefresh、および実行中runtimeのsymlinkを保護する。
自動GCは行わない。手動で`.blex`を削除するときは全dev/stage processを終了しておく。

## 10. `blex dev`

### 10.1 CLI

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

`--`はblex optionとBlenderへの追加引数を分けるdelimiterであり、Blenderへは渡さない。

### 10.2 dev lock

`dev`開始時に`.blex/locks/dev.lock`をOS file lockする。

- 同じprojectで別の`dev`が動作中なら待機せずエラーにする。
- lock fileは残してよいが、process終了時にOS lockを解放する。
- `stage`との同時実行は許可する。

### 10.3 開発用user resources

最終runtime rootは`.blex/runtime_environment`とする。

- `--user-resources`、`[tool.blex.dev].user-resources`の順でseed directoryを決定する。
- seed指定がなければ空のruntime rootを作成する。
- seed指定があれば、毎回その内容全体を新しい一時directoryへcopyする。
- seed内symlinkは実体化する。
- リンク切れまたはcopy cycleはエラーにする。
- seedはproject外でもよい。
- seedとruntime rootが包含関係にある場合はエラーにする。
- 新runtimeの準備が完了してから既存のBlex管理runtime rootを置き換える。
- 前回runtimeは再利用しない。
- runtime rootはBlender終了後も残す。

Blender child processには次を適用する。

- `BLENDER_USER_RESOURCES`をruntime rootの絶対pathへ設定する。
- 親環境から`BLENDER_USER_CONFIG`、`BLENDER_USER_SCRIPTS`、`BLENDER_USER_EXTENSIONS`、`BLENDER_USER_DATAFILES`を除去する。
- その他の環境変数は継承する。
- 親の既存`BLENDER_USER_RESOURCES`をseed入力として暗黙利用しない。

### 10.4 開発用local repository

開発対象は既存repositoryへ配置せず、固定した専用repositoryを使用する。

```text
repository module: blex_dev
repository name:   Blex Development
repository root:   .blex/runtime_environment/extensions/blex_dev
extension root:    .blex/runtime_environment/extensions/blex_dev/<manifest-id>
Python module:     bl_ext.blex_dev.<manifest-id>
```

- seedをcopyした直後に`extensions/blex_dev`が既に存在すれば、変更、削除、再利用せずエラーにする。
- コピーされたBlender preferencesにmodule`blex_dev`のrepository設定が既に存在すれば、変更、削除、再利用せずbootstrapを失敗させる。
- repository rootはblexが新規作成する。
- bootstrapはremote URLを持たないlocal USER repositoryとして追加する。
- preferencesをdiskへ保存しない。
- remote syncを行わない。
- local repositoryのrefreshだけを行う。
- 他repositoryのdirectoryや設定には触れない。
- 同じManifest IDのExtensionが他の登録済みrepositoryに存在する場合は、enabled状態にかかわらず衝突エラーにする。
- 他repositoryの同名Extensionを無効化、削除、上書きしない。

### 10.5 Extension配置

runtimeのExtension rootには次を配置する。

1. blexが生成した`blender_manifest.toml`
2. source-dir内の`blender_manifest.toml`とtop-level`wheels/`を除く全entry
3. 元Manifestから参照される手書きWheel
4. 自動収集したWheel

- hidden fileとhidden directoryも対象にする。
- runtime`wheels/`は常に実directoryとして作る。
- source`wheels/`内のうちManifestから参照されないfileは配置しない。
- 生成Manifestは元Manifestを複製し、`platforms`を現在の1 Platformへ置き換え、`wheels`をmerge結果へ置き換える。
- 元Manifestは変更しない。
- `wheels`が0件ならfieldを削除または省略する。
- source entryとWheelの相対pathが競合し、同一fileとして安全にdeduplicateできない場合はエラーにする。

### 10.6 link mode

`link-mode`はExtension source entryとWheel fileの配置方法を1 deployment全体に対して選ぶ。

`auto`:

```text
Windows:
  symlink
  copy

macOS/Linux:
  symlink
  copy
```

- symlink作成が1件でも失敗した場合は、不完全な一時runtimeを破棄し、deployment全体をcopyで作り直す。
- fallback時はwarningを表示する。
- 明示`symlink`ではfallbackせずエラーにする。
- 明示`copy`では最初から全対象をcopyする。
- symlinkはsourceの変更をBlenderから参照可能にする。
- copyは`dev`起動時に1度だけ行い、Blender実行中の再同期やfile watcherを提供しない。
- copy時はmetadataを保持するcopyを用い、symlinkを実体化する。

### 10.7 Blender executableと起動

Blender executableは次の順で決定する。

1. CLI`--blender`
2. `[tool.blex].blender`
3. 組み込み値`blender`

解決できなければ起動前にエラーにする。他のinstall locationを探索しない。

Blend fileはCLI`--blend-file`、`[tool.blex.dev].blend-file`の順で決定し、指定時は存在する通常fileでなければエラーにする。

起動commandは次の順序とする。

```text
<blender>
    [<blend-file>]
    --python-exit-code 1
    --python <absolute-path-to-.blex/bootstrap/dev.py>
    [<BLENDER_ARGS>...]
```

- bootstrapは`.blex/bootstrap/dev.py`へ毎回原子的に再生成し、終了後も残す。
- commandはshellを介さず実行する。
- child processのstdoutとstderrは継承する。
- blexはBlender終了まで親processとして待機する。
- 追加Blender引数は加工せず末尾へ追加する。

### 10.8 bootstrap

bootstrapは標準ライブラリとBlenderの`bpy`だけで開始できるself-contained scriptとする。

処理順は次のとおりとする。

1. `blex_dev` repositoryの衝突を検査する。
2. 他repositoryに同一Manifest IDがないことを検査する。
3. local repositoryを追加してrefreshする。
4. debugpy有効時は第10.10節のserverを開始する。
5. `bl_ext.blex_dev.<manifest-id>`を`bpy.ops.preferences.addon_enable`で有効化する。
6. reload-panel有効時はreload operatorとpanelを登録する。

- Blender versionの明示検査は行わない。
- repository追加、refresh、debugpy、初回Extension有効化、panel登録の失敗はbootstrapから伝播させる。
- `--python-exit-code 1`により、初期bootstrap失敗時はBlenderを終了コード1で終了させる。
- bootstrapはpreferencesを保存しない。

### 10.9 Reload panel

reload-panelは既定で有効とし、`--no-reload-panel`または設定で無効化できる。

UIは次の値を使用する。

- sidebar category: `Reload`
- panel label: `Reload Extension`
- button label: `Reload`
- operator ID: `blex.reload_extension`

operatorは次の順序で処理する。

1. module名を`bl_ext.blex_dev.<manifest-id>`とする。symlink配置の場合は、無効化の前にsource tree全体のpath安全性を再検証し、source直下の追加・削除・renameをruntimeのlinkへ反映する。生成Manifest、wheels、runtime生成fileは管理対象に含めず、未知のfileとの衝突はエラーにする。copy配置では同期しない。
2. `bpy.ops.preferences.addon_disable(module=module_name)`を実行する。
3. `importlib.invalidate_caches()`を実行する。
4. `sys.modules`からmodule名そのものと`module_name + "."`で始まる名前を収集する。
5. dot数の降順に並べ、深いsubmoduleから`sys.modules.pop(name, None)`する。
6. purge件数と各module名をstdoutへ表示する。
7. `bpy.ops.preferences.addon_enable(module=module_name)`を実行する。

purge logは次の形式とする。

```text
[reload_extension] purged modules: <count>
  - <module-name>
```

button操作中の失敗はoperator内で捕捉し、Blender reportとstdout/stderrへ表示する。Reload失敗だけでBlender processを終了しない。copy modeではsourceの再copyを行わない。

### 10.10 debugpy

debugpyは既定で無効とする。

- versionは`1.8.21`に固定する。
- 同梱artifactは`debugpy-1.8.21-py2.py3-none-any.whl`とする。
- artifact SHA-256は`b1e37d333663c8851516a47364ef473da127f9caebe4417e6df6f5825a7e9a92`とする。
- debugpyのlicense fileを同梱する。
- debugpy有効時に展開先directoryが存在しない場合だけ、同梱Wheelを検証・展開する。
- host側で`.blex/debugpy`内の一時directoryへ`zipfile`で安全に展開し、完成後に`.blex/debugpy/1.8.21`へrenameする。
- archive memberが展開先外へ出るpathならエラーにする。
- `.blex/debugpy/1.8.21`がdirectoryとして存在するかだけを確認し、存在すれば内容を検証せず再利用する。
- 展開済みdirectory用のmetadataは作成せず、file欠落、追加file、hash不一致などの破損検査を行わない。存在するdirectoryの自動修復、削除、再展開、およびnetwork取得を行わない。
- bootstrapは展開directoryを`sys.path`先頭へ追加してdebugpyをimportする。
- `debugpy.listen((host, port))`を呼ぶ。
- client接続を待機しない。
- 既定hostは`127.0.0.1`、既定portは`5678`とする。
- listen失敗は初期bootstrap失敗としてBlenderを終了させる。

### 10.11 process supervisionとsignal

- POSIXではBlenderを新しいprocess groupで起動する。
- Windowsでは`CREATE_NEW_PROCESS_GROUP`を使用する。
- Ctrl+C受信時、POSIXではchild groupへSIGINT、WindowsではCTRL_BREAK_EVENTを送る。
- 5秒待機して終了しなければterminateし、さらに5秒待機して終了しなければkillする。
- Ctrl+C処理後のblex終了コードは130とする。
- 通常のBlender終了は終了コードをそのまま返す。
- process終了後にdev lockを解放する。

## 11. `blex stage`

### 11.1 CLI

```text
blex stage [DIRECTORY]
    [--source-dir PATH]
    [--output-dir PATH]
    [--wheels | --no-wheels]
    [--refresh-wheels]
```

Blender executable option、Blender Python検出option、Blenderへの追加引数は提供しない。

### 11.2 stage lock

`stage`開始時に`.blex/locks/stage.lock`をOS file lockする。

- 同じprojectで別の`stage`が動作中なら待機せずエラーにする。
- `dev`との同時実行は許可する。
- Wheel cacheの競合はfingerprint単位lockで調停する。
- stage終了時にOS lockを解放する。

### 11.3 output-dir

output-dirはCLI`--output-dir`、`[tool.blex.stage].output-dir`、組み込み値`dist`の順で決定する。

実体解決後、次をすべて満たさなければエラーにする。

- project rootの厳密な子孫である。
- project rootそのものではない。
- source-dirと同一、source-dirの祖先、source-dirの子孫ではない。
- `.blex`と同一、`.blex`の祖先、`.blex`の子孫ではない。
- project root直下の`.git`と同一、`.git`の祖先、`.git`の子孫ではない。
- project rootからoutput-dirまでの既存componentがsymlinkではない。
- `<output-dir>/source`がsymlinkではない。
- output-dirまたは選択targetが存在する通常fileならエラーにする。

blexが削除・置換できるのは、`<output-dir>/source`とblex自身の一時・backup directoryだけとする。output-dir内の他のfile・directoryは変更しない。

### 11.4 staging構造

実行中のPython・Platformの組に対して次を作成する。

```text
<output-dir>/
└── source/
    ├── blender_manifest.toml
    ├── <Extension source entries>
    └── wheels/
```

- `source/` directory全体を現在環境向けの内容で置換する。
- source-dir全体を実fileとしてcopyする。
- 内部symlinkは第4.2節を検証した上で実体化する。
- 元Manifestをcopy後に生成Manifestで置き換える。
- 元source-dirと元Manifestは変更しない。
- Manifest`[build].paths_exclude_pattern`はblexのcopy除外規則として使用しない。
- `__pycache__`やhidden fileを含め、source-dirの全内容をcopyする。
- source側の手書きWheelを保持し、自動Wheelをmergeする。
- `wheels/`は`--no-wheels`でも作成する。
- 生成Manifestの`platforms`は当該source treeの1 Platformの配列へ置き換える。
- 生成Manifestの`wheels`はmerge結果へ置き換え、0件なら省略する。
- Blender commandとZIP作成は実行しない。
- この`source/`が保持する最終stagingである。

### 11.5 現在環境のtransaction

stage command全体を1 transactionとして扱う。

1. UUID4の小文字32桁hexを`run-id`とし、`.blex/staging/stage/<run-id>`の`prepared`で現在環境のsource treeを完成させる。
2. 現在環境のvalidation、Wheel取得、copy、Manifest生成が成功するまでoutput-dirを変更しない。
3. `.blex/staging/stage/<run-id>/transaction.json`へ対象、既存targetのbackup path、commit進捗を記録する。
4. 選択targetの既存directoryをtransaction内の`backup`へrenameする。
5. 新しいdirectoryをtargetへrenameする。
6. targetのcommit成功後にbackupとcommit記録を削除する。

- preparation中の失敗では全一時directoryを削除し、既存outputを変更しない。
- 捕捉可能なcommit失敗やCtrl+Cでは、完了済みrenameを逆順にrollbackし、対象directoryを元へ戻す。
- 既存targetは毎回自動的に再生成し、stageにはforce optionを設けない。
- 通常の例外と捕捉可能な割り込みについて、対象directoryを成功または無変更とする。
- process強制終了、power loss、OS crash中のcrash consistencyは保証しない。
- stage lock取得後、`.blex/staging/stage`に前回runのentryが1つでも残っていればabandoned transactionとみなし、自動削除、自動復旧、処理続行を行わず、関連pathを表示してエラーにする。

## 12. managed directoryと排他

blexがproject内で管理する領域は次のとおりとする。

```text
.blex/
├── .gitignore
├── bootstrap/
│   └── dev.py
├── cache/
│   └── blender-python.json
├── debugpy/
│   └── 1.8.21/
├── locks/
│   ├── dev.lock
│   ├── stage.lock
│   ├── wheel-objects/
│   │   └── <sha256>.lock
│   └── wheels/
│       └── <fingerprint>.lock
├── wheel-objects/
│   └── <sha256>/<filename>
├── runtime_environment/
├── staging/
│   └── stage/
└── wheels/
    └── <fingerprint>/
        ├── metadata.json
        └── *.whl
```

- directoryは必要になった時だけ作る。
- recursive deleteまたはreplace前に、対象の絶対pathが期待するmanaged root内にあることを再検証する。
- unresolved環境変数、glob、project root、source-dirをrecursive delete対象にしない。
- managed root自体がsymlinkならエラーにする。
- `.blex/.gitignore`は存在判定より先にsymlink/reparse pointを検査し、リンク切れも拒否する。
- dev lockとstage lockは別であり、`dev`と`stage`は同時実行できる。
- cache lockはdev/stage間で共有する。
- lock取得は待機しない。
- OS lockはprocess異常終了時にもOSによって解放される。lock file自体の存在だけでstale判定しない。

## 13. 品質保証

### 13.1 静的検査とformat

```toml
[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E4", "E7", "E9", "F"]

[tool.pyrefly]
project-includes = ["src", "tests"]
search-path = ["src"]
python-version = "3.11"
preset = "strict"
```

- `ruff format --check .`を実行する。
- `ruff check .`を実行する。
- `pyrefly check`を実行する。
- Pyreflyのbaseline、全体ignore、error suppressionを初期状態で設けない。

### 13.2 test

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = [
    "--strict-config",
    "--strict-markers",
    "--cov=blex",
    "--cov-report=term-missing",
    "--cov-fail-under=90",
]
```

- test frameworkはpytestとする。
- coverageはblex package全体のline coverage 90%以上を必須とする。
- unit testで命名、path containment、設定優先順位、TOML merge、Manifest merge、Platform mapping、cache fingerprint、hash検証、transaction計画を検証する。
- fake Blender executableで内部Pythonバージョン検出、timeout、起動引数、終了コード、signal supervisionを検証する。
- fake pip moduleを別processで起動し、command、cache、失敗、stdout/stderrを検証する。
- ローカルで生成したWheelだけを使い、実pipによる直接・推移的marker、extras、Python条件、native互換タグと依存versionの選択、cache refresh中の参照を回帰テストする。
- 実pipのtestではPIP_NO_INDEXとローカルWheel置き場を設定し、外部サービスへ接続しない。
- filesystem integration testでsymlink、copy、lock、rollback、output safetyを検証する。
- debugpy同梱Wheelのfilename、SHA-256、license、package artifactへの収録をtestする。
- test実行中にPyPI、Blender server、その他networkへ接続しない。
- 実Blender testは通常test suiteとCIで実行しない。
- 環境変数`BLEX_TEST_BLENDER`へ実行バイナリを明示したローカル実行だけ、実Blender integration testを有効にする。

### 13.3 CI

GitHub Actionsで次を実行する。

- Windows x64、macOS x64、Linux x64
- Python 3.11と3.14のtest matrix
- `actions/setup-python`でPythonを選択し、pipでpackageとdev dependency groupをinstallする。
- 各matrixでtestとサンプルExtensionのnative stageを実行し、生成したsource treeをartifactとして保存する。
- 別jobで`python -m build`を実行し、sdistとWheelをartifactとして保存する。
- 別jobでRuff format、Ruff lint、Pyrefly
- Windows ARM64、macOS ARM64、Wheel target Platformはmockを用いたunit test
- 実Blender downloadおよび実Blender testなし

使用するrunner labelは実際にx64 architectureを提供するlabelへ明示的に固定し、`*-latest`が別architectureへ変化することに依存しない。

## 14. 完了条件

実装は次をすべて満たした時点で完了とする。

- 本仕様の3コマンドと全optionが実装されている。
- runtimeとcacheが`.blex`以下だけに配置される。
- Manifest schema defaultが`1.0.0`、Manifest version defaultが`1.0.0`である。
- stageは`<output-dir>/source`だけをtransactionで置換し、output-dir内の他のfile・directoryを保持する。
- `dev`と`stage`の依存解決を実行中のPythonのpipに固定し、runtimeにuvを必要としない。
- source Manifestを`dev`または`stage`が変更しない。
- `stage`がBlenderを実行せず、stagingを保持する。
- runtime dependencyが`tomlkit`以外に増えていない。
- debugpyが既定で無効であり、同梱1.8.21だけを使用する。
- 全test、coverage、Ruff、Pyreflyが成功する。
