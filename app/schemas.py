"""Pydanticスキーマ(API入出力の定義)。

ai_type の許可値チェックは str を継承した Enum で行う。
許可外の値や空文字の title はFastAPIが自動で 422 を返す。
"""
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AiType(str, Enum):
    CHATGPT = "ChatGPT"
    CLAUDE = "Claude"
    CLAUDE_CODE = "Claude Code"
    GEMMA = "Gemma"
    QWEN = "Qwen"
    OTHER = "Other"


class LogCreate(BaseModel):
    """ログ登録のリクエストボディ。

    - title: 必須。min_length=1 で空文字を拒否(422)
    - ai_type: 必須。Enum外の値は422
    - tags / note: 省略可。未指定時は空文字で保存
    - tags は保存前に正規化される(下記 normalize_tags 参照)
    """
    title: str = Field(min_length=1, max_length=200)
    ai_type: AiType
    tags: str = ""
    note: str = ""

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: str) -> str:
        """tags をカンマ区切りとして正規化する。

        - 各タグの前後空白を除去
        - 空タグ(連続カンマや空白のみ)を除去
        - 正規化後は ", " 区切りで保存
        例: " python , AI,, Claude " → "python, AI, Claude"

        バリデーション層で正規化することで、DBには常に
        正規化済みの値だけが入ることを保証する(将来のタグ
        正規化・別テーブル移行時にデータ掃除が不要になる)。
        """
        tags = [tag.strip() for tag in value.split(",")]
        return ", ".join(tag for tag in tags if tag)


class LogResponse(BaseModel):
    """ログのレスポンス。created_at はサーバ側で自動設定された値を返す"""
    id: int
    title: str
    ai_type: str
    tags: str
    note: str
    created_at: datetime

    # SQLAlchemyモデルのインスタンスから直接変換できるようにする
    model_config = ConfigDict(from_attributes=True)
