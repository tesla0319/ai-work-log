"""AIログノートのAPIテスト。

テスト用にin-memory SQLiteを使い、本番DBファイルとは完全に分離する。
各テスト関数の前後でテーブルを作り直し、テスト間のデータ干渉を防ぐ。
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import AiLog

# StaticPool: in-memory SQLiteは接続ごとに別DBになってしまうため、
# 全接続で同じ1つの接続を使い回す設定が必要
test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_database():
    """各テストの前にテーブルを作成し、後に削除する(テスト独立性の確保)"""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


def create_sample(title="テストログ", ai_type="Claude"):
    """テストデータ登録のヘルパー"""
    return client.post(
        "/api/logs",
        json={"title": title, "ai_type": ai_type, "tags": "test", "note": "メモ"},
    )


def insert_log_with_created_at(created_at: datetime, title="時刻テスト") -> int:
    """created_at を固定値で指定してDBに直接登録するヘルパー。

    created_at はAPI経由では指定できない(サーバ時刻で自動設定)ため、
    JST表示のような時刻依存のテストではDBへ直接挿入する。
    DB上の保存値と同じく naive UTC を渡すこと。
    """
    db = TestSessionLocal()
    try:
        log = AiLog(title=title, ai_type="Claude", created_at=created_at)
        db.add(log)
        db.commit()
        return log.id
    finally:
        db.close()


# --- 1. 登録成功 ---
def test_create_log_success():
    res = create_sample()
    assert res.status_code == 201
    body = res.json()
    assert body["id"] == 1
    assert body["title"] == "テストログ"
    assert body["ai_type"] == "Claude"
    assert body["created_at"] is not None  # サーバ側で自動設定されること


def test_create_log_optional_fields_default_to_empty():
    """tags / note 省略時は空文字で保存される"""
    res = client.post("/api/logs", json={"title": "最小登録", "ai_type": "Other"})
    assert res.status_code == 201
    body = res.json()
    assert body["tags"] == ""
    assert body["note"] == ""


# --- 2. 必須項目不足 ---
def test_create_log_missing_title():
    res = client.post("/api/logs", json={"ai_type": "Claude"})
    assert res.status_code == 422


def test_create_log_missing_ai_type():
    res = client.post("/api/logs", json={"title": "タイトルのみ"})
    assert res.status_code == 422


# --- 3. 許可外 ai_type は 422 ---
def test_create_log_invalid_ai_type():
    res = client.post("/api/logs", json={"title": "不正な種別", "ai_type": "GPT-4"})
    assert res.status_code == 422


# --- 4. title が空文字の場合は 422 ---
def test_create_log_empty_title():
    res = client.post("/api/logs", json={"title": "", "ai_type": "Claude"})
    assert res.status_code == 422


# --- 4-1. title の空白処理 ---
def test_create_log_whitespace_only_title_rejected():
    """空白のみの title は 422"""
    res = client.post("/api/logs", json={"title": "   ", "ai_type": "Claude"})
    assert res.status_code == 422


def test_create_log_title_stripped():
    """title の前後空白は除去して保存される"""
    res = client.post("/api/logs", json={"title": "  前後空白  ", "ai_type": "Claude"})
    assert res.status_code == 201
    assert res.json()["title"] == "前後空白"


# --- 4-2. title の境界値(max_length=200) ---
def test_create_log_title_200_chars_success():
    """境界値: 200文字ちょうどは登録成功"""
    res = client.post("/api/logs", json={"title": "a" * 200, "ai_type": "Claude"})
    assert res.status_code == 201
    assert len(res.json()["title"]) == 200


def test_create_log_title_201_chars_rejected():
    """境界値+1: 201文字は 422"""
    res = client.post("/api/logs", json={"title": "a" * 201, "ai_type": "Claude"})
    assert res.status_code == 422


# --- 4-3. tags の正規化 ---
def test_create_log_tags_normalized():
    """tags は前後空白除去・空タグ除去のうえ ", " 区切りで保存される"""
    res = client.post(
        "/api/logs",
        json={
            "title": "タグ正規化テスト",
            "ai_type": "Claude",
            "tags": " python , AI,, Claude ",
        },
    )
    assert res.status_code == 201
    assert res.json()["tags"] == "python, AI, Claude"

    # 一覧/詳細でも正規化済みの値が返ること(DB保存値の確認)
    log_id = res.json()["id"]
    detail = client.get(f"/api/logs/{log_id}")
    assert detail.json()["tags"] == "python, AI, Claude"


def test_create_log_tags_only_commas_becomes_empty():
    """空タグのみ(カンマと空白だけ)の場合は空文字になる"""
    res = client.post(
        "/api/logs",
        json={"title": "空タグテスト", "ai_type": "Claude", "tags": " , ,, "},
    )
    assert res.status_code == 201
    assert res.json()["tags"] == ""


# --- 4-4. tags / note の長さ境界値 ---
def test_create_log_tags_500_chars_success():
    """境界値: tags 500文字ちょうどは登録成功"""
    res = client.post(
        "/api/logs",
        json={"title": "tags境界値", "ai_type": "Claude", "tags": "a" * 500},
    )
    assert res.status_code == 201


def test_create_log_tags_501_chars_rejected():
    """境界値+1: tags 501文字は 422"""
    res = client.post(
        "/api/logs",
        json={"title": "tags境界値超過", "ai_type": "Claude", "tags": "a" * 501},
    )
    assert res.status_code == 422


def test_create_log_note_10000_chars_success():
    """境界値: note 10000文字ちょうどは登録成功"""
    res = client.post(
        "/api/logs",
        json={"title": "note境界値", "ai_type": "Claude", "note": "a" * 10_000},
    )
    assert res.status_code == 201


def test_create_log_note_10001_chars_rejected():
    """境界値+1: note 10001文字は 422"""
    res = client.post(
        "/api/logs",
        json={"title": "note境界値超過", "ai_type": "Claude", "note": "a" * 10_001},
    )
    assert res.status_code == 422


# --- 5. 一覧取得(登録日時の降順) ---
def test_list_logs_ordered_desc():
    create_sample(title="1件目")
    create_sample(title="2件目")
    create_sample(title="3件目")

    res = client.get("/api/logs")
    assert res.status_code == 200
    titles = [log["title"] for log in res.json()]
    # 後に登録したものが先頭に来ること
    assert titles == ["3件目", "2件目", "1件目"]


# --- 6. 詳細取得 ---
def test_get_log_detail():
    log_id = create_sample(title="詳細テスト").json()["id"]

    res = client.get(f"/api/logs/{log_id}")
    assert res.status_code == 200
    assert res.json()["title"] == "詳細テスト"


# --- 7. 存在しないID は 404 ---
def test_get_log_not_found():
    res = client.get("/api/logs/9999")
    assert res.status_code == 404


# --- 8. タイトル検索(部分一致) ---
def test_search_logs_by_title_partial_match():
    create_sample(title="Claude Codeの使い方")
    create_sample(title="ChatGPTの設定")
    create_sample(title="Claudeのプロンプト集")

    res = client.get("/api/logs", params={"title": "Claude"})
    assert res.status_code == 200
    titles = [log["title"] for log in res.json()]
    assert len(titles) == 2
    assert "ChatGPTの設定" not in titles


# --- 9. 検索結果0件は空配列 ---
def test_search_logs_no_match_returns_empty_list():
    create_sample(title="ヒットしないデータ")

    res = client.get("/api/logs", params={"title": "存在しないキーワード"})
    assert res.status_code == 200
    assert res.json() == []


# --- 10. 一覧画面(Phase 1) ---
def test_list_page_returns_200():
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]


def test_list_page_shows_title():
    """登録済みログのタイトルが一覧画面に表示される"""
    create_sample(title="画面表示テスト")

    res = client.get("/")
    assert res.status_code == 200
    assert "画面表示テスト" in res.text


def test_list_page_shows_ai_type():
    """登録済みログの ai_type が一覧画面に表示される"""
    create_sample(title="種別表示テスト", ai_type="Claude Code")

    res = client.get("/")
    assert res.status_code == 200
    assert "Claude Code" in res.text


def test_list_page_escapes_html_in_title():
    """titleにHTMLタグが含まれてもエスケープされる(XSS対策の確認)"""
    create_sample(title="<script>alert(1)</script>")

    res = client.get("/")
    assert res.status_code == 200
    # タグそのものは出力されず、エスケープされた形で含まれること
    assert "<script>alert(1)</script>" not in res.text
    assert "&lt;script&gt;" in res.text


def test_list_page_empty_shows_message():
    """0件時の一覧画面に空メッセージが表示される"""
    res = client.get("/")
    assert res.status_code == 200
    assert "まだログがありません。" in res.text


# --- 10-2. created_at のJST表示 ---
def test_list_page_shows_created_at_in_jst():
    """一覧画面: naive UTC の created_at がJST(+9時間)で表示される"""
    insert_log_with_created_at(datetime(2026, 1, 2, 3, 4, 0), title="JST一覧テスト")

    res = client.get("/")
    assert res.status_code == 200
    # UTC 2026-01-02 03:04 → JST 2026-01-02 12:04
    assert "2026-01-02 12:04 JST" in res.text


def test_detail_page_shows_created_at_in_jst_with_date_rollover():
    """詳細画面: JST変換で日付が翌日に繰り上がるケースも正しく表示される"""
    log_id = insert_log_with_created_at(
        datetime(2026, 6, 1, 20, 30, 0), title="JST詳細テスト"
    )

    res = client.get(f"/logs/{log_id}")
    assert res.status_code == 200
    # UTC 2026-06-01 20:30 → JST 2026-06-02 05:30(日付が変わる)
    assert "2026-06-02 05:30 JST" in res.text


# --- 11. 詳細画面(Phase 2) ---
def test_detail_page_returns_200():
    log_id = create_sample(title="詳細画面テスト").json()["id"]

    res = client.get(f"/logs/{log_id}")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]


def test_detail_page_shows_all_fields():
    """詳細画面に title / ai_type / tags / note が表示される"""
    res = client.post(
        "/api/logs",
        json={
            "title": "全項目表示テスト",
            "ai_type": "Claude Code",
            "tags": "python, pytest",
            "note": "詳細画面のメモ本文",
        },
    )
    log_id = res.json()["id"]

    page = client.get(f"/logs/{log_id}")
    assert page.status_code == 200
    assert "全項目表示テスト" in page.text
    assert "Claude Code" in page.text
    assert "python, pytest" in page.text
    assert "詳細画面のメモ本文" in page.text


def test_detail_page_escapes_html_in_note():
    """noteにHTMLタグが含まれてもエスケープされる(詳細画面のXSS対策の確認)"""
    res = client.post(
        "/api/logs",
        json={
            "title": "note XSSテスト",
            "ai_type": "Claude",
            "note": "<script>alert(1)</script>",
        },
    )
    log_id = res.json()["id"]

    page = client.get(f"/logs/{log_id}")
    assert page.status_code == 200
    # タグそのものは出力されず、エスケープされた形で含まれること
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text


def test_detail_page_not_found_returns_404_with_back_link():
    """存在しないIDは404。一覧へ戻るリンク付きのHTML画面を返す"""
    res = client.get("/logs/9999")
    assert res.status_code == 404
    assert "text/html" in res.headers["content-type"]
    assert 'href="/"' in res.text  # 一覧へ戻るリンクがあること


def test_list_page_has_link_to_detail():
    """一覧画面のカードに詳細画面へのリンクが表示される"""
    log_id = create_sample(title="リンクテスト").json()["id"]

    res = client.get("/")
    assert res.status_code == 200
    assert f'href="/logs/{log_id}"' in res.text


# --- 12. 登録画面(Phase 3) ---
def test_new_log_page_returns_200():
    res = client.get("/logs/new")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]


def test_new_log_page_has_title_input():
    """登録画面に title の入力欄が存在する"""
    res = client.get("/logs/new")
    assert 'name="title"' in res.text


def test_new_log_page_has_ai_type_select_with_allowed_values():
    """登録画面に ai_type の select があり、許可値が選択肢として並ぶ"""
    res = client.get("/logs/new")
    assert 'name="ai_type"' in res.text
    assert "<select" in res.text
    for value in ["ChatGPT", "Claude", "Claude Code", "Gemma", "Qwen", "Other"]:
        assert f'<option value="{value}"' in res.text


def test_list_page_has_link_to_new():
    """一覧画面に新規登録リンクが表示される"""
    res = client.get("/")
    assert res.status_code == 200
    assert 'href="/logs/new"' in res.text


def test_submit_form_success_redirects_to_list():
    """フォーム登録成功時は一覧画面(/)へ303リダイレクトし、データが保存される"""
    res = client.post(
        "/logs/new",
        data={"title": "フォーム登録", "ai_type": "Claude", "tags": "form", "note": "メモ"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert res.headers["location"] == "/"

    # APIと同じバリデーション・保存経路を通っていることを確認
    logs = client.get("/api/logs").json()
    assert logs[0]["title"] == "フォーム登録"
    assert logs[0]["ai_type"] == "Claude"


def test_submit_form_invalid_shows_error_and_keeps_input():
    """バリデーションエラー時は422で同じ画面にエラー表示し、入力値を保持する"""
    res = client.post(
        "/logs/new",
        data={"title": "   ", "ai_type": "Claude", "tags": "python", "note": "メモ"},
    )
    assert res.status_code == 422
    assert "text/html" in res.headers["content-type"]
    assert 'class="errors"' in res.text  # エラーメッセージ欄が表示される
    assert "タイトル" in res.text  # どの項目のエラーかが分かる
    assert 'value="python"' in res.text  # 入力したtagsが保持される


def test_submit_form_tags_normalized():
    """フォーム経由でもAPIと同じtags正規化が適用される(LogCreate共用の確認)"""
    client.post(
        "/logs/new",
        data={"title": "フォーム正規化", "ai_type": "Other", "tags": " a ,, b "},
        follow_redirects=False,
    )
    logs = client.get("/api/logs").json()
    assert logs[0]["tags"] == "a, b"
