"""DB接続とセッション管理。

SQLite + SQLAlchemy の最小構成。
テスト時は dependency_overrides でこの get_db を差し替える。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# SQLiteのDBファイルはプロジェクト直下に作成される
DATABASE_URL = "sqlite:///./ai_log_note.db"

# check_same_thread=False:
# FastAPIは1リクエスト内で別スレッドからDBを触ることがあるため、
# SQLiteのスレッドチェックを無効化する(SQLite利用時の定番設定)
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """全モデルの基底クラス"""


def get_db():
    """リクエストごとにDBセッションを払い出し、終了時に必ず閉じる"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
