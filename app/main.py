"""AIログノート MVP。

エンドポイント数が少ないため、レイヤー分割せず
このファイルにルーティングを集約している(過剰設計回避)。

DB取得処理は fetch_logs() に共通化し、
API(/api/logs)と画面(/)の両方から呼ぶ(二重実装の防止)。
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.database import Base, engine, get_db
from app.models import AiLog
from app.schemas import AiType, LogCreate, LogResponse

# MVPのためマイグレーションツールは使わず、起動時にテーブルを作成する。
# 既存テーブルがあれば何もしない(データは消えない)。
Base.metadata.create_all(bind=engine)


def ensure_columns() -> None:
    """後からモデルに追加した列を既存テーブルに補う(最小マイグレーション)。

    create_all は「テーブルが無ければ作る」だけで、既存テーブルへの
    列追加はしないため、Phase 6 で追加した next_action 列をここで補う。
    既存データは削除せずそのまま保持される。
    スキーマ変更が頻繁になってきたら Alembic の導入を検討する。
    """
    with engine.begin() as conn:
        # PRAGMA table_info の各行は (cid, name, type, ...)。name は添字1
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(ai_logs)")}
        if "next_action" not in existing:
            conn.exec_driver_sql(
                "ALTER TABLE ai_logs ADD COLUMN next_action TEXT NOT NULL DEFAULT ''"
            )


ensure_columns()

app = FastAPI(title="AI Log Note")

# テンプレートの場所を app/templates に固定する。
# __file__ 基準にすることで、どのディレクトリから起動しても動く
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# 日本標準時(UTC+9)。標準ライブラリのみで定義できる(zoneinfo不要)
JST = timezone(timedelta(hours=9), name="JST")


def format_jst(dt: datetime) -> str:
    """naive UTC の datetime を JST の表示文字列に変換する。

    タイムゾーン方針:
    - DB上の created_at は naive UTC(タイムゾーン情報なしのUTC時刻)
    - APIレスポンスは保存値のまま返す(UTC、オフセット表記なし)
    - 画面表示のみ、この関数で JST に変換する

    SQLiteから読み出した datetime はタイムゾーン情報を持たないため、
    まず UTC として解釈し直してから JST に変換する。
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")


# テンプレート内で {{ log.created_at | jst }} と書けるようにフィルタ登録する
templates.env.filters["jst"] = format_jst


# --- 共通関数(APIと画面で共有するDB取得処理) ---

def fetch_logs(
    db: Session,
    title: str | None = None,
    ai_type: str | None = None,
) -> list[AiLog]:
    """ログを登録日時の降順で取得する。

    - title 指定時: タイトル部分一致で絞り込む
    - ai_type 指定時: AI種別の完全一致で絞り込む
    - 両方指定時: AND条件で絞り込む
    - 未指定・空文字の条件は無視する(絞り込まない)

    API(GET /api/logs)と画面(GET /)の両方から呼ばれる共通関数。
    並び順などの仕様変更はこの関数だけ直せば両方に反映される。
    """
    query = db.query(AiLog)
    if title:
        # contains は LIKE '%xxx%' に展開される(部分一致)
        query = query.filter(AiLog.title.contains(title))
    if ai_type:
        # AI種別は完全一致。許可外の値を指定した場合は単に0件になる
        query = query.filter(AiLog.ai_type == ai_type)
    return query.order_by(AiLog.created_at.desc(), AiLog.id.desc()).all()


def fetch_log(db: Session, log_id: int) -> AiLog | None:
    """ログを1件取得する。存在しなければ None を返す。

    API(GET /api/logs/{log_id})と画面(GET /logs/{log_id})の共通関数。
    「見つからない場合の応答」はAPIとHTMLで異なるため、
    404の組み立ては呼び出し側の責務とし、この関数は取得だけを行う。
    """
    return db.get(AiLog, log_id)


def insert_log(db: Session, payload: LogCreate) -> AiLog:
    """検証済みデータからログを1件登録する。

    API(POST /api/logs)とフォーム(POST /logs/new)の共通関数。
    バリデーションは LogCreate の生成時に済んでいる前提で、
    この関数は保存だけを行う。
    """
    log = AiLog(
        title=payload.title,
        ai_type=payload.ai_type.value,  # Enum -> str に変換して保存
        tags=payload.tags,
        note=payload.note,
        next_action=payload.next_action,
    )
    db.add(log)
    db.commit()
    db.refresh(log)  # DBで採番された id と created_at を取得し直す
    return log


# --- API ---

@app.post("/api/logs", response_model=LogResponse, status_code=201)
def create_log(payload: LogCreate, db: Session = Depends(get_db)):
    """ログ登録。created_at はモデルのdefault(サーバ時刻)で自動設定される"""
    return insert_log(db, payload)


@app.get("/api/logs", response_model=list[LogResponse])
def list_logs(
    title: str | None = None,
    ai_type: str | None = None,
    db: Session = Depends(get_db),
):
    """ログ一覧(登録日時の降順)。

    - title: タイトル部分一致で絞り込み
    - ai_type: AI種別の完全一致で絞り込み(許可外の値は0件になるだけ)
    - 両方指定した場合はAND条件
    該当0件でも404にせず空配列を返す(一覧系APIの一般的な挙動)。
    """
    return fetch_logs(db, title, ai_type)


@app.get("/api/logs/{log_id}", response_model=LogResponse)
def get_log(log_id: int, db: Session = Depends(get_db)):
    """ログ詳細。存在しないIDは404"""
    log = fetch_log(db, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Log not found")
    return log


# --- 画面(Phase 1: 一覧 / Phase 2: 詳細 / Phase 3: 登録フォーム) ---

def render_new_log_page(
    request: Request,
    values: dict[str, str],
    errors: list[str],
    status_code: int = 200,
):
    """登録フォームを描画する。初回表示とエラー時の再表示で共用。

    values: フォームに表示する入力値(エラー時は入力内容を保持して返す)
    errors: 画面上部に表示するエラーメッセージのリスト
    """
    return templates.TemplateResponse(
        request=request,
        name="new.html",
        context={
            # select の選択肢。Enumから生成するので許可値とズレない
            "ai_types": [ai_type.value for ai_type in AiType],
            "values": values,
            "errors": errors,
        },
        status_code=status_code,
    )


def format_validation_errors(exc: ValidationError) -> list[str]:
    """Pydanticのバリデーションエラーを画面表示用の文言に変換する"""
    labels = {
        "title": "タイトル",
        "ai_type": "AI種別",
        "tags": "タグ",
        "note": "メモ",
        "next_action": "次回作業メモ",
    }
    messages = []
    for error in exc.errors():
        field = str(error["loc"][0]) if error["loc"] else ""
        messages.append(f"{labels.get(field, field)}: {error['msg']}")
    return messages

@app.get("/", response_class=HTMLResponse)
def list_page(
    request: Request,
    title: str | None = None,
    ai_type: str | None = None,
    db: Session = Depends(get_db),
):
    """一覧画面。APIと同じ fetch_logs() を使う(二重実装の防止)。

    title(部分一致)と ai_type(完全一致)で絞り込める
    (API GET /api/logs と同じ挙動)。未指定・空文字は絞り込みなし。

    Jinja2の自動エスケープが有効なため、title等にHTMLタグが
    含まれていてもそのまま文字として表示される(XSS対策)。
    テンプレート内で safe フィルタは使用禁止。
    """
    logs = fetch_logs(db, title, ai_type)
    return templates.TemplateResponse(
        request=request,
        name="list.html",
        context={
            "logs": logs,
            # title / ai_type は検索フォームへの再表示用(入力状態を保持する)
            "title": title or "",
            "ai_type": ai_type or "",
            # selectの選択肢。登録フォームと同じくEnumから生成する
            "ai_types": [t.value for t in AiType],
        },
    )


# 注意: /logs/new は /logs/{log_id} より「先に」定義すること。
# ルートは定義順に照合されるため、後に定義すると "new" が
# log_id として解釈され、int変換に失敗して422になってしまう。
@app.get("/logs/new", response_class=HTMLResponse)
def new_log_page(request: Request):
    """登録フォーム画面(初回表示)"""
    return render_new_log_page(
        request=request,
        values={"title": "", "ai_type": "", "tags": "", "note": "", "next_action": ""},
        errors=[],
    )


@app.post("/logs/new", response_class=HTMLResponse)
def create_log_from_form(
    request: Request,
    title: str = Form(""),
    ai_type: str = Form(""),
    tags: str = Form(""),
    note: str = Form(""),
    next_action: str = Form(""),
    db: Session = Depends(get_db),
):
    """登録フォームの送信を受け付ける。

    バリデーションはAPIと同じ LogCreate を手動で生成して行う
    (検証ルールの二重実装を防ぐ)。
    - 成功: 一覧画面へリダイレクト(303 See Other: POST後のGET誘導)
    - 失敗: 入力値とエラーメッセージ付きで同じフォームを422で再表示
    """
    try:
        payload = LogCreate(
            title=title, ai_type=ai_type, tags=tags, note=note, next_action=next_action
        )
    except ValidationError as exc:
        return render_new_log_page(
            request=request,
            values={
                "title": title,
                "ai_type": ai_type,
                "tags": tags,
                "note": note,
                "next_action": next_action,
            },
            errors=format_validation_errors(exc),
            status_code=422,
        )
    insert_log(db, payload)
    return RedirectResponse(url="/", status_code=303)


@app.get("/logs/{log_id}", response_class=HTMLResponse)
def detail_page(request: Request, log_id: int, db: Session = Depends(get_db)):
    """詳細画面。APIと同じ fetch_log() を使う。

    存在しないIDの場合は、一覧へ戻るリンク付きの404画面を返す
    (画面利用者にJSONエラーを見せないため、API側の404とは応答を分ける)。
    """
    log = fetch_log(db, log_id)
    if log is None:
        return templates.TemplateResponse(
            request=request,
            name="not_found.html",
            context={},
            status_code=404,
        )
    return templates.TemplateResponse(
        request=request,
        name="detail.html",
        context={"log": log},
    )
