from collections.abc import Generator
from contextvars import ContextVar, Token
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker, with_loader_criteria

from app.config import settings

_current_user_id_var: ContextVar[int] = ContextVar("current_user_id", default=1)


def get_current_user_id() -> int:
    return _current_user_id_var.get()


def set_current_user_id(user_id: int) -> Token[int]:
    return _current_user_id_var.set(user_id)


def reset_current_user_id(token: Token[int]) -> None:
    _current_user_id_var.reset(token)


def _build_engine():
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(settings.database_url, connect_args=connect_args, future=True)

    if settings.database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.close()

    return engine


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Shared declarative base for all modules in the modular monolith."""


def _iter_tenant_models():
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        if getattr(cls, "__tenant_scoped__", False):
            yield cls


@event.listens_for(Session, "before_flush")
def _assign_tenant_user_id(session: Session, _flush_context, _instances) -> None:
    uid = session.info.get("user_id") or get_current_user_id()
    for obj in session.new:
        if getattr(obj, "__tenant_scoped__", False) and getattr(obj, "user_id", None) is None:
            obj.user_id = uid


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_filter(execute_state) -> None:
    if execute_state.execution_options.get("skip_tenant_filter", False):
        return
    uid = execute_state.session.info.get("user_id") or get_current_user_id()
    if execute_state.is_select:
        opts = [
            with_loader_criteria(
                cls,
                cls.user_id == uid,
                include_aliases=True,
            )
            for cls in _iter_tenant_models()
        ]
        if opts:
            execute_state.statement = execute_state.statement.options(*opts)
    elif execute_state.is_update or execute_state.is_delete:
        stmt = execute_state.statement
        entity = stmt.table if hasattr(stmt, "table") else None
        if entity is not None:
            for cls in _iter_tenant_models():
                if cls.__table__ is entity:
                    execute_state.statement = stmt.where(cls.user_id == uid)
                    break


def get_db() -> Generator[Session, None, None]:
    """Yield a transactional database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
