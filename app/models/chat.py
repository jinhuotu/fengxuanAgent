"""会话和消息表；不在模型中执行查询或业务判断。"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.mysql import MEDIUMTEXT, TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.chat import CHAT_TITLE_LENGTH
from app.db.base import Base

# 新手注意：测试的 SQLite 只认 INTEGER 自增，生产仍使用 BIGINT。
IDENTIFIER_TYPE = BigInteger().with_variant(Integer, "sqlite")
STATUS_TYPE = TINYINT().with_variant(Integer, "sqlite")


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("idx_chat_sessions_user_updated", "user_id", "is_deleted", "updated_at", "id"),
        Index("idx_chat_sessions_active_expiry", "is_deleted", "generation_expires_at"),
        {"comment": "对话会话：记录用户归属、历史版本和当前生成请求"},
    )
    id: Mapped[int] = mapped_column(IDENTIFIER_TYPE, primary_key=True, autoincrement=True, comment="会话编号，自增")
    user_id: Mapped[int] = mapped_column(BigInteger, comment="所属用户编号，由服务校验，不建物理外键")
    title: Mapped[str] = mapped_column(String(CHAT_TITLE_LENGTH), default="新对话", comment="会话标题，首次提问后截取问题生成")
    history_version: Mapped[int] = mapped_column(Integer, default=0, comment="历史版本号，隔离不同版本的上下文缓存")
    active_request_id: Mapped[str | None] = mapped_column(String(36), default=None, comment="正在生成的请求编号，空表示未占用")
    generation_expires_at: Mapped[datetime | None] = mapped_column(DateTime, default=None, comment="生成占用的到期时间，到期后标记中断")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间，东八区")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间，东八区")
    is_deleted: Mapped[int] = mapped_column(STATUS_TYPE, default=0, server_default="0", comment="软删除标记，零表示正常，一表示删除")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "request_id", "role", name="uk_chat_messages_request_role"),
        Index("idx_chat_messages_session_id", "session_id", "is_deleted", "id"),
        {"comment": "对话消息：保存问题、回答、用量及完成或中断状态"},
    )
    id: Mapped[int] = mapped_column(IDENTIFIER_TYPE, primary_key=True, autoincrement=True, comment="消息编号，自增，作为会话内排序依据")
    session_id: Mapped[int] = mapped_column(BigInteger, comment="所属会话编号，由服务校验，不建物理外键")
    request_id: Mapped[str] = mapped_column(String(36), comment="客户端请求编号，同一次问题和回答共用编号")
    role: Mapped[int] = mapped_column(STATUS_TYPE, comment="消息角色，一表示用户，二表示助手")
    content: Mapped[str] = mapped_column(Text().with_variant(MEDIUMTEXT, "mysql"), default="", comment="消息正文，失败或中断时可保存已生成的部分回答")
    status: Mapped[int] = mapped_column(STATUS_TYPE, comment="消息状态，一生成中，二完成，三失败，四中断")
    model: Mapped[str | None] = mapped_column(String(100), default=None, comment="助手回答使用的模型标识，用户消息为空")
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, default=None, comment="模型输入令牌数，未知时为空")
    completion_tokens: Mapped[int | None] = mapped_column(Integer, default=None, comment="模型输出令牌数，未知时为空")
    finish_reason: Mapped[str | None] = mapped_column(String(40), default=None, comment="模型结束原因，例如正常结束或达到长度上限")
    error_code: Mapped[str | None] = mapped_column(String(40), default=None, comment="安全的错误分类，不保存上游原始错误内容")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间，东八区")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间，东八区")
    is_deleted: Mapped[int] = mapped_column(STATUS_TYPE, default=0, server_default="0", comment="软删除标记，零表示正常，一表示删除")


class ChatGenerationGate(Base):
    __tablename__ = "chat_generation_gates"
    __table_args__ = {"comment": "对话生成准入锁：固定记录协调多个进程的全站并发检查"}
    id: Mapped[int] = mapped_column(IDENTIFIER_TYPE, primary_key=True, autoincrement=True, comment="准入锁编号，迁移创建编号一的固定记录")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), comment="创建时间，东八区")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间，东八区")
    is_deleted: Mapped[int] = mapped_column(STATUS_TYPE, default=0, server_default="0", comment="软删除标记，固定记录必须保持零")

