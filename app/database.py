import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./recovery.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine_kwargs = {"connect_args": connect_args}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["poolclass"] = StaticPool
engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def migrate_sqlite_schema():
    """Safely add supporting demo columns without destroying existing data."""
    if not DATABASE_URL.startswith("sqlite"):
        return
    with engine.begin() as connection:
        columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(transactions)")}
        for column_name, ddl in {
            "recovered_amount": "ALTER TABLE transactions ADD COLUMN recovered_amount FLOAT NOT NULL DEFAULT 0",
            "decision_source": "ALTER TABLE transactions ADD COLUMN decision_source VARCHAR",
            "decision_rationale": "ALTER TABLE transactions ADD COLUMN decision_rationale TEXT",
            "failure_reason": "ALTER TABLE transactions ADD COLUMN failure_reason VARCHAR",
            "recovery_attempt_count": "ALTER TABLE transactions ADD COLUMN recovery_attempt_count INTEGER NOT NULL DEFAULT 0",
            "updated_at": "ALTER TABLE transactions ADD COLUMN updated_at DATETIME",
        }.items():
            if column_name not in columns:
                connection.exec_driver_sql(ddl)


def initialize_database():
    """Ensure SQLite tables exist before the app or tests use the DB."""
    Base.metadata.create_all(bind=engine)
    migrate_sqlite_schema()


initialize_database()


def get_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
