"""AIログノート MVP。

エンドポイント数が少ないため、レイヤー分割せず
このファイルにルーティングを集約している(過剰設計回避)。

DB取得処理は fetch_logs() に共通化し、
API(/api/logs)と画面(/)の両方から呼ぶ(二重実装の防止)。
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import Base, engine, get_db
from app.models import AiLog
from app.schemas import LogCreate, LogResponse

# MVPのためマイグレーションツールは使わず、起動時にテーブルを作成する。
# 既存テーブルがあれば何もしない(データは消えない)。
Base.metadata.create_all(bind=engine)

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

def fetch_logs(db: Session, title: str | None = None) -> list[AiLog]:
    """ログを登録日時の降順で取得する。title指定時はタイトル部分一致で絞り込む。

    API(GET /api/logs)と画面(GET /)の両方から呼ばれる共通関数。
    並び順などの仕様変更はこの関数だけ直せば両方に反映される。
    """
    query = db.query(AiLog)
    if title:
        # contains は LIKE '%xxx%' に展開される(部分一致)
        query = query.filter(AiLog.title.contains(title))
    return query.order_by(AiLog.created_at.desc(), AiLog.id.desc()).all()


def fetch_log(db: Session, log_id: int) -> AiLog | None:
    """ログを1件取得する。存在しなければ None を返す。

    API(GET /api/logs/{log_id})と画面(GET /logs/{log_id})の共通関数。
    「見つからない場合の応答」はAPIとHTMLで異なるため、
    404の組み立ては呼び出し側の責務とし、この関数は取得だけを行う。
    """
    return db.get(AiLog, log_id)


# --- API ---

@app.post("/api/logs", response_model=LogResponse, status_code=201)
def create_log(payload: LogCreate, db: Session = Depends(get_db)):
    """ログ登録。created_at はモデルのdefault(サーバ時刻)で自動設定される"""
    log = AiLog(
        title=payload.title,
        ai_type=payload.ai_type.value,  # Enum -> str に変換して保存
        tags=payload.tags,
        note=payload.note,
    )
    db.add(log)
    db.commit()
    db.refresh(log)  # DBで採番された id と created_at を取得し直す
    return log


@app.get("/api/logs", response_model=list[LogResponse])
def list_logs(title: str | None = None, db: Session = Depends(get_db)):
    """ログ一覧(登録日時の降順)。

    title クエリパラメータがあればタイトル部分一致で絞り込む。
    該当0件でも404にせず空配列を返す(一覧系APIの一般的な挙動)。
    """
    return fetch_logs(db, title)


@app.get("/api/logs/{log_id}", response_model=LogResponse)
def get_log(log_id: int, db: Session = Depends(get_db)):
    """ログ詳細。存在しないIDは404"""
    log = fetch_log(db, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Log not found")
    return log


# --- 画面(Phase 1: 一覧 / Phase 2: 詳細) ---

@app.get("/", response_class=HTMLResponse)
def list_page(request: Request, db: Session = Depends(get_db)):
    """一覧画面。APIと同じ fetch_logs() を使う(二重実装の防止)。

    Jinja2の自動エスケープが有効なため、title等にHTMLタグが
    含まれていてもそのまま文字として表示される(XSS対策)。
    テンプレート内で safe フィルタは使用禁止。
    """
    logs = fetch_logs(db)
    return templates.TemplateResponse(
        request=request,
        name="list.html",
        context={"logs": logs},
    )


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
