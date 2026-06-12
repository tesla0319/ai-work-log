# AIログノート

AIとの会話や作業内容を記録するためのシンプルなREST APIアプリ(MVP)です。
記録したログの登録・一覧・詳細表示・タイトル検索ができます。

## 技術スタック

| 分類 | 技術 |
|---|---|
| Webフレームワーク | FastAPI |
| DB | SQLite |
| ORM | SQLAlchemy |
| バリデーション | Pydantic |
| テスト | pytest |

## セットアップ手順

```bash
# 1. リポジトリのディレクトリに移動
cd ai-log-note

# 2. 仮想環境の作成と有効化(推奨)
python -m venv venv
source venv/bin/activate  # Windowsの場合: venv\Scripts\activate

# 3. 依存パッケージのインストール
pip install -r requirements.txt
```

## 起動方法

```bash
uvicorn app.main:app --reload
```

- 起動後: http://127.0.0.1:8000
- APIドキュメント(Swagger UI): http://127.0.0.1:8000/docs
- 初回起動時、プロジェクト直下に `ai_log_note.db` が自動作成されます

## テスト実行方法

```bash
python -m pytest tests/ -v
```

テストはin-memory SQLiteで実行されるため、本番DBファイルには影響しません。

## API一覧

### POST /api/logs — ログ登録

リクエストボディ(JSON):

| 項目 | 必須 | 説明 |
|---|---|---|
| title | ○ | タイトル(1〜200文字)。前後空白は除去して保存。空文字・空白のみは不可 |
| ai_type | ○ | AIの種別(下記の許可値のみ) |
| tags | - | タグ(カンマ区切り文字列、最大500文字。省略時は空文字)。保存前に正規化されます: 各タグの前後空白を除去、空タグを除去し、`", "` 区切りで保存(例: `" python , AI,, Claude "` → `"python, AI, Claude"`) |
| note | - | メモ(最大10000文字。省略時は空文字) |

`created_at` はサーバ側で自動設定されます(リクエストでの指定不可)。

```bash
curl -X POST http://127.0.0.1:8000/api/logs \
  -H "Content-Type: application/json" \
  -d '{"title": "プロンプト改善メモ", "ai_type": "Claude", "tags": "prompt,tips", "note": "..."}'
```

- 成功: `201` 登録されたログを返す
- バリデーションエラー(必須項目不足・空または空白のみのtitle・許可外ai_type・長さ上限超過): `422`

### GET /api/logs — ログ一覧

登録日時の降順で全件を返します。0件の場合は空配列 `[]` を返します。

```bash
curl http://127.0.0.1:8000/api/logs
```

### GET /api/logs/{log_id} — ログ詳細

```bash
curl http://127.0.0.1:8000/api/logs/1
```

- 成功: `200`
- 存在しないID: `404`

### GET /api/logs?title=xxx — タイトル検索

タイトルの部分一致で絞り込みます。該当0件でも `404` にはならず空配列を返します。

```bash
curl "http://127.0.0.1:8000/api/logs?title=Claude"
```

## ai_type の許可値

以下の6種類のみ登録可能です。それ以外の値は `422` エラーになります。

- `ChatGPT`
- `Claude`
- `Claude Code`
- `Gemma`
- `Qwen`
- `Other`

## 画面(Phase 1〜2)

- `GET /` : ログ一覧画面。カードのタイトルから詳細画面へ移動できます
- `GET /logs/{log_id}` : ログ詳細画面。存在しないIDは一覧へ戻るリンク付きの404画面を表示します

スマホ前提のカード型・縦並びレイアウトです。

> **重要**: 画面はローカル利用前提です。認証を実装していないため、**外部公開時は認証(最低でもBasic認証)の実装が必須**です。

## MVPの制約

- **認証なし**: 誰でも全APIにアクセス可能です。公開環境での利用は想定していません
- **タグ検索なし**: tagsはカンマ区切りの文字列として保存しているため、タグでの絞り込みはできません(要件化された時点で別テーブルへの正規化を検討)
- **ページネーションなし**: 一覧は常に全件返却です。件数増加時は `limit/offset` の追加を推奨
