# FMOD Studio MCP Server

FMOD Studio の Scripting Terminal（TCP:3663）経由で FMOD Studio を操作する MCP サーバーです。
Claude Code などの MCP クライアントから、FMOD Studio 上で JavaScript を実行できます。

## Architecture

```
MCP Client (Claude Code など)
   │  stdio (JSON-RPC)
   ▼
fmod-studio-mcp (Python / MCP SDK v2: MCPServer)  ← uv / uvx で起動
   │  TCP:3663 (JavaScript)
   ▼
FMOD Studio (内蔵 Scripting Terminal)
```

- **MCP サーバー**: MCP クライアントからツール呼び出しを受け取り、JavaScript を FMOD Studio の Scripting Terminal に TCP:3663 で送信し、結果を JSON で返します。
- **FMOD Studio**: 内蔵の Scripting Terminal が TCP:3663 で待ち受け、受信した JavaScript を評価します。**FMOD Studio 側に追加スクリプトは不要です。**

## Requirements

- FMOD Studio（Scripting Terminal が有効なもの）
- [uv](https://docs.astral.sh/uv/)
  - Python 3.10+ が必要ですが、手元に無い場合は uv が自動で取得します。
- MCP サーバーは FMOD Studio と同じホストで動かすことを想定しています（既定の接続先は `localhost:3663`）。

### uv のインストール

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

macOS / Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Usage

MCP サーバーは MCP クライアントが stdio で起動します。手動でサーバーを起動しておく必要はありません。
事前に FMOD Studio を起動し、プロジェクトを開いておいてください。

### A. GitHub から直接起動する（推奨）

リポジトリのクローンは不要です。`uvx` が GitHub から取得・キャッシュして起動します。

Claude Code に登録:

```bash
claude mcp add fmod-studio -- uvx --from git+https://github.com/yuke-x68/fmod-studio-mcp fmod-studio-mcp
```

環境変数を指定する場合:

```bash
claude mcp add fmod-studio -e FMOD_MCP_HOST=localhost -e FMOD_MCP_PORT=3663 -- uvx --from git+https://github.com/yuke-x68/fmod-studio-mcp fmod-studio-mcp
```

プロジェクト単位で共有したい場合は、プロジェクトルートの `.mcp.json` に記述します（[`mcp_settings_example.json`](mcp_settings_example.json) と同じ内容です）:

```json
{
  "mcpServers": {
    "fmod-studio": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/yuke-x68/fmod-studio-mcp",
        "fmod-studio-mcp"
      ],
      "env": {
        "FMOD_MCP_HOST": "localhost",
        "FMOD_MCP_PORT": "3663"
      }
    }
  }
}
```

特定のバージョンに固定したい場合は、URL の末尾にタグやコミットを付けます（例: `git+https://github.com/yuke-x68/fmod-studio-mcp@<commit-or-tag>`）。

#### 最新版への更新

`uvx` は取得したパッケージをキャッシュします。リポジトリの更新を取り込むには、一度 `--refresh` 付きで実行してください:

```bash
uvx --refresh --from git+https://github.com/yuke-x68/fmod-studio-mcp fmod-studio-mcp --help
```

### B. ローカルのクローンから起動する

コードを編集しながら使う場合はこちら。

```bash
git clone https://github.com/yuke-x68/fmod-studio-mcp.git
```

Claude Code に登録（パスはクローン先の絶対パスに置き換えてください）:

```bash
claude mcp add fmod-studio -- uv run --directory C:\path\to\fmod-studio-mcp fmod-studio-mcp
```

`uv run` は初回起動時に `.venv` を自動作成し、依存関係をインストールします。

### 動作確認

```bash
uvx --from git+https://github.com/yuke-x68/fmod-studio-mcp fmod-studio-mcp --help
```

Claude Code では `/mcp` で `fmod-studio` が接続済みになっていることを確認できます。

## Configuration

### 環境変数

| Env Var | Default | Description |
|---------|---------|-------------|
| `FMOD_MCP_HOST` | `localhost` | FMOD Studio のホスト |
| `FMOD_MCP_PORT` | `3663` | TCP ポート（FMOD Studio の固定ポート） |
| `FMOD_MCP_TIMEOUT_S` | `10.0` | コマンドの既定タイムアウト（秒） |
| `FMOD_MCP_CONNECT_TIMEOUT_S` | `5.0` | TCP 接続タイムアウト（秒） |
| `FMOD_MCP_RECV_BUFFER` | `65536` | TCP 受信バッファサイズ（バイト） |
| `FMOD_MCP_MAX_RETRIES` | `2` | 接続失敗時の再試行回数 |
| `FMOD_MCP_RETRY_DELAY_S` | `1.0` | 再試行の間隔（秒） |

### コマンドライン引数

| Option | Description |
|--------|-------------|
| `-v`, `--verbose` | ログレベルを DEBUG にする |
| `--log-level LEVEL` | ログレベルを指定（`DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`）。`--verbose` より優先。既定は `WARNING` |

ログは stderr に出力されます（stdout は MCP の JSON-RPC 通信に使用）。
引数は `uvx ... fmod-studio-mcp --log-level INFO` のようにコマンド末尾に付けます。

## Tools

### `execute_script_tool`

FMOD Studio の Scripting Terminal で任意の JavaScript を実行します。

| 引数 | 型 | 既定値 | 説明 |
|------|----|--------|------|
| `script` | string | — | 実行する JavaScript。トップレベルで `return` した値が結果として返ります |
| `timeout_ms` | int | `5000` | 実行タイムアウト（ミリ秒） |

スクリプトは try-catch と `JSON.stringify` でラップされて実行され、結果は次のいずれかの JSON 文字列で返ります:

```jsonc
{"status": "ok", "result": <value>}
{"status": "blocked", "error": {"code": "BLOCKED", "message": "...", "detail": {...}}}
{"status": "error", "error": {"code": "<CODE>", "message": "...", "detail": {...}}}
```

エラーコード: `SYNTAX_ERROR`, `RUNTIME_ERROR`, `TIMEOUT`, `PARSE_ERROR`, `CONNECTION_ERROR`

例:

```js
return studio.project.model.Event.findInstances().map(function (e) { return e.getPath(); });
```

#### セキュリティポリシー

実行前にスクリプトを静的にスキャンし、以下の API 呼び出しを含むものは FMOD Studio に送信せずブロックします（[`bridge/security.py`](src/fmod_studio_mcp/bridge/security.py)）。

| ブロック対象 | 理由 |
|--------------|------|
| `triggerAction(...)` | タイムラインのトランスポート操作 |
| `studio.system.start()` / `stop()` / `record()` | トランスポート・録音の制御 |
| `studio.project.close()` | プロジェクトを閉じる |
| `studio.project.delete()` | プロジェクト要素の破壊的削除 |
| `studio.project.new()` | 現在のプロジェクトを置き換える |
| `.open(...)` | ファイルシステムへの読み書き（`File.open()` など） |

これは正規表現による簡易的なゲートであり、変数経由の間接呼び出しなどは検出できません。

## Development

```bash
uv sync          # .venv の作成と依存関係（開発用含む）のインストール
uv run pytest    # テスト実行
```

## License

[MIT](LICENSE)
