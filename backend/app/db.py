"""数据库基础设施：Base、engine、session 工厂、顺序 ID 分配表。

`app.db` 是**基础设施**，不属于任何领域；架构规则 ARCH-04/05 只约束 `app.core`
与 `app.global_domain` 的依赖方向，不涉及本模块。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Integer, String, create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool


class Base(DeclarativeBase):
    """所有 ORM 模型的声明基类。"""


class IdSequence(Base):
    """顺序号分配表。

    文档要求人类可读的连续 ID（`RL-0001`、`M1-SE-001`、`E-0001`），
    这类 ID 无法用 UUID 替代，因此需要一张带事务语义的计数器表。
    单用户场景下 SQLite 的写事务已足够；迁到 Postgres 时加 `SELECT ... FOR UPDATE` 即可。
    """

    __tablename__ = "id_sequence"

    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


def default_database_url() -> str:
    """默认落到 `backend/var/mouxue.db`；可用 `MOUXUE_DATABASE_URL` 覆盖。"""
    override = os.environ.get("MOUXUE_DATABASE_URL")
    if override:
        return override
    root = Path(__file__).resolve().parent.parent
    var_dir = root / "var"
    var_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite+pysqlite:///{var_dir / 'mouxue.db'}"


def make_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """创建 engine。内存库用 StaticPool，避免每个连接看到不同的空库。"""
    url = url or default_database_url()
    kwargs: dict[str, object] = {"echo": echo, "future": True}
    if url.endswith(":memory:"):
        kwargs["poolclass"] = StaticPool
        kwargs["connect_args"] = {"check_same_thread": False}
    if url.startswith("sqlite") and ":memory:" not in url:
        # ★ 不用连接池：每个请求一条新连接。
        #
        # 现场：刚建出来的会话，紧接着的 PATCH 报「会话不存在」，而 GET /state 又读得到；
        # 同一时刻的列表也少这一条，过一会儿再试全好。这是**旧连接看旧快照**：
        # WAL 下读事务一旦开着就一直看那一刻的库，连接池却把这种连接留着复用。
        # 单用户本地应用没有并发收益可言，省掉连接池就把这类"偶尔读不到刚写的东西"消掉。
        kwargs["poolclass"] = NullPool
        kwargs["pool_pre_ping"] = True
    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):
        # SQLite 默认不强制外键，必须显式打开——否则 RESTRICT 形同虚设。
        #
        # 另外两条是**并发**用的，都是被现场逼出来的：
        # - `journal_mode=WAL`：读写不再互相阻塞。默认的 rollback journal 下，
        #   一个写事务会挡住所有其它写，而这里有一类特别长的写事务 ——
        #   **讨论在等模型回答的那几十秒**。于是"讨论跑着的时候改设置"会拿到
        #   `database is locked`，界面上是一次 500。
        # - `busy_timeout=5000`：拿不到锁先等 5 秒再报错。默认是 0，即"别人正在写就
        #   立刻失败"，而那种失败在用户眼里就是"这台机器随机报错"。
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):  # pragma: no cover
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            # 内存库不支持 WAL（也没有意义），只在文件库上开。
            if ":memory:" not in url:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def ensure_forward_columns(engine: Engine) -> None:
    """SQLite 的**前向补列**：只加可空列，不改/不删。

    项目没有 Alembic；`create_all()` 不会给既有表加列。动机从
    “内外混合 + 强度”改成“前置基础 + 价值 + 障碍”时，旧库必须能继续打开，
    否则用户看到的不是新字段，而是一张打不开的库。

    只做 additive 的 TEXT 列（`session_motivation.foundation/value`、
    `llm_profile.models`）；将来若需要改类型/约束，再写真正的迁移，
    不把这里扩成万能改表器。
    """
    if engine.dialect.name != "sqlite":
        return
    from sqlalchemy import inspect, text

    with engine.begin() as conn:
        inspector = inspect(conn)
        if inspector.has_table("session_motivation"):
            columns = {column["name"] for column in inspector.get_columns("session_motivation")}
            if "foundation" not in columns:
                conn.execute(text("ALTER TABLE session_motivation ADD COLUMN foundation TEXT"))
            if "value" not in columns:
                conn.execute(text('ALTER TABLE session_motivation ADD COLUMN "value" TEXT'))
        if inspector.has_table("llm_profile"):
            columns = {column["name"] for column in inspector.get_columns("llm_profile")}
            if "models" not in columns:
                # 旧档案只有 `model` 一个；读模型时 `_normalize_models` 会把它
                # 当成唯一的勾选项，所以这里允许空串，不强行回填。
                conn.execute(text("ALTER TABLE llm_profile ADD COLUMN models TEXT"))
        if inspector.has_table("self_assessment_run"):
            columns = {
                column["name"]
                for column in inspector.get_columns("self_assessment_run")
            }
            # v1 的历史运行是 44 条整卷：两列留空就是"v1 / 未分档"，
            # 不拿 `full` 去回填 —— 那会让旧的 44 条看起来像 127 条。
            if "tier" not in columns:
                conn.execute(text("ALTER TABLE self_assessment_run ADD COLUMN tier TEXT"))
            if "scale_version" not in columns:
                conn.execute(
                    text("ALTER TABLE self_assessment_run ADD COLUMN scale_version TEXT")
                )


class DbSequenceStore:
    """`core.ids.SequenceStore` 协议的数据库实现。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def next(self, scope: str) -> int:
        row = self._session.get(IdSequence, scope)
        if row is None:
            row = IdSequence(scope=scope, next_value=0)
            self._session.add(row)
            self._session.flush()
        elif not isinstance(row, IdSequence):
            raise TypeError(f"scope={scope!r} 未映射到 IdSequence")
        row.next_value += 1
        self._session.flush()
        return row.next_value

    def peek(self, scope: str) -> int:
        value = self._session.scalar(
            select(IdSequence.next_value).where(IdSequence.scope == scope)
        )
        return int(value or 0)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """事务边界：正常提交，异常回滚。"""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
