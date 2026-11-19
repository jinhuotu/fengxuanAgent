"""数据库连接与会话管理。

全项目只有这里创建连接池。别处再写一次 create_engine，就会多一个池子，
连接数翻倍，出问题时也不知道是谁占着连接。
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

# 做什么：创建进程内唯一的连接池
# 入参：连接串来自 config.database_url（mysql+pymysql 驱动 + utf8mb4）
# 返回：Engine 对象，本身不连库，第一次取连接时才真正连
engine = create_engine(
    settings.database_url,
    # 取连接前先 ping 一次。MySQL 会主动断开空闲太久的连接，
    # 没有这一项，隔夜之后第一个请求必然报 "server has gone away"。
    pool_pre_ping=True,
    # 每小时回收一次连接，比 MySQL 默认的 wait_timeout（8 小时）更早，双保险
    pool_recycle=3600,
    # 不打印每条 SQL。调试时改成 True 看，但要记得改回来
    echo=False,
)

# 做什么：会话工厂，调用一次产出一次数据库会话
# 入参：无
# 返回：SessionLocal 类，用法是 SessionLocal()
# autoflush=False：查询前不自动刷盘，SQL 什么时候发出去由我们显式控制，行为可预测
# expire_on_commit=False：commit 之后对象里的属性还能读，不用每条都重新查一次
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# 做什么：FastAPI 依赖，给每个请求发一个会话，请求结束一定关掉
# 入参：无
# 返回：生成器，yield 出 Session；异常时也会执行 finally
# 为什么用 yield：把"关闭连接"这件事绑定在请求生命周期上，写业务时不用记着 close
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
