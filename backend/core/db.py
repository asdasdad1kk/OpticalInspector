from sqlalchemy import inspect, text
from sqlmodel import SQLModel, Session, create_engine

from core.config import DATABASE_URL, ensure_dirs

ensure_dirs()

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={
        "check_same_thread": False,
        "timeout": 30,
    },
)

# Columnas agregadas despues de la primera version: (tabla, columna, tipo).
_MIGRATIONS = [
    ("inspection", "original_path", "VARCHAR"),
    ("inspection", "rectified_path", "VARCHAR"),
    ("inspection", "overlay_path", "VARCHAR"),
]


def get_session():
    with Session(engine) as session:
        yield session


def migrate_schema() -> None:
    """ALTER TABLE minimo para bases creadas con versiones anteriores."""
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, column, column_type in _MIGRATIONS:
            if table not in inspector.get_table_names():
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            if column not in existing:
                connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {column_type}'))


def create_database():
    SQLModel.metadata.create_all(engine)
    migrate_schema()
