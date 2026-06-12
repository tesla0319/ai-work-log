"""SQLAlchemyモデル定義。

ai_type はDB上はただのTEXTとして保存する。
SQLiteにENUM型がないため、許可値のチェックは
Pydantic(schemas.py)側で行う方針。
"""
from datetime import datetime, timezone

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utc_now() -> datetime:
    """登録日時用。テストで差し替えやすいよう関数に切り出している"""
    return datetime.now(timezone.utc)


class AiLog(Base):
    __tablename__ = "ai_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    ai_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # tags はMVPではカンマ区切りの文字列として保存(未指定は空文字)
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utc_now)
