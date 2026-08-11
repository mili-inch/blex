# Blender Extension Development CLI

## 1. Abstruct

この文書はBlender Extension開発に必要な処理を持つPython製CLIプロジェクトの仕様を記述する。

CLIとPythonパッケージの名称を`blex`とする。

## 2. Project Structure

blexが構成するディレクトリ構造は以下とする。

```text
my-extension/
├── pyproject.toml
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
└── .blex/
    ├── .gitignore
    └── environment/
        ├── config/
        ├── scripts/
        ├── datafiles/
        └── extensions/
```

## 3. Commands

### blex init

```bash
blex init [DIRECTORY] [OPTIONS]
```

ディレクトリに対してblexに必要な情報を初期化する。

CLIのインターフェースはuv initを基準に合わせる。
ディレクトリが未指定の場合、現在のディレクトリに初期化を行う。

対象となるディレクトリに既に`pyproject.toml`や`blender_manifest.toml`が存在した場合は、なるべく既存の構造を破壊することなく必要なフィールドを追加する。

CLIによる対話的な設定機能を持つ。また、対話モード時の機能と対応する、非対話的な設定も可能とする。

以下のような機能を持つ。

- 実行対象Blenderパスの取得
  - コマンドオプションによる指定
  - 環境変数による指定
  - PATHによる取得
- 特定のBlenderバイナリに依存しない初期化

- Blender内部Pythonバージョン・環境の取得と保持
- Blender API型スタブの導入
- `pyproject.toml`の初期化
- `blender_manifest.toml`の生成

#### blex build
#### blex dev


Blenderのパスを指定して保持する機能と
BlenderからPythonバージョン情報を捕捉する機能
Pythonバージョンを保持する機能
は作って
devやbuildはPythonバージョンとBlenderパスを引数として実行するようにして、設定されている場合はそれを用いるように
