import logging
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

logger = logging.getLogger("calling.database")

# Render provides DATABASE_URL starting with postgres://, but SQLAlchemy requires postgresql://
DATABASE_URL = os.environ.get("DATABASE_URL")
is_render = bool(os.environ.get("RENDER"))

if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./calling_app.db"
elif not is_render and "dpg-" in DATABASE_URL and ".render.com" not in DATABASE_URL:
    # Render internal URLs (dpg-*) only resolve inside Render's private cloud network.
    # On a local machine, attempting to connect will freeze on DNS lookup for 15+ seconds.
    logger.warning(
        "[Database] Render Internal Database URL detected in local environment. "
        "Render internal hostnames only resolve inside Render's cloud network. "
        "Falling back to local SQLite ('sqlite:///./calling_app.db') for local development. "
        "To connect to Render PostgreSQL from your computer, use the 'External Database URL' from Render."
    )
    DATABASE_URL = "sqlite:///./calling_app.db"
elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Dependency generator that yields a database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _consolidate_and_migrate_flagged_numbers(eng):
    """Consolidate duplicate scam numbers and ensure required columns/indices exist."""
    from sqlalchemy import inspect, text

    try:
        inspector = inspect(eng)
        if not inspector.has_table("flagged_numbers"):
            return

        columns = {col["name"]: col for col in inspector.get_columns("flagged_numbers")}
        with eng.begin() as conn:
            # 1. Add fake_detection_count if missing
            if "fake_detection_count" not in columns:
                conn.execute(
                    text("ALTER TABLE flagged_numbers ADD COLUMN fake_detection_count INTEGER NOT NULL DEFAULT 1")
                )

            # 2. Add or migrate last_flagged_at if missing
            if "last_flagged_at" not in columns:
                if "flagged_at" in columns:
                    try:
                        conn.execute(
                            text("ALTER TABLE flagged_numbers RENAME COLUMN flagged_at TO last_flagged_at")
                        )
                    except Exception:
                        conn.execute(text("ALTER TABLE flagged_numbers ADD COLUMN last_flagged_at TIMESTAMP"))
                        conn.execute(
                            text("UPDATE flagged_numbers SET last_flagged_at = flagged_at WHERE last_flagged_at IS NULL")
                        )
                else:
                    conn.execute(text("ALTER TABLE flagged_numbers ADD COLUMN last_flagged_at TIMESTAMP"))

            # 3. Consolidate duplicate phone numbers before enforcing uniqueness
            duplicates = conn.execute(
                text("SELECT phone_number FROM flagged_numbers GROUP BY phone_number HAVING COUNT(*) > 1")
            ).fetchall()
            for row in duplicates:
                phone = row[0]
                records = conn.execute(
                    text(
                        "SELECT id, fake_detection_count FROM flagged_numbers WHERE phone_number = :phone ORDER BY id DESC"
                    ),
                    {"phone": phone},
                ).fetchall()
                if len(records) > 1:
                    latest_id = records[0][0]
                    total_count = sum(r[1] or 1 for r in records)
                    conn.execute(
                        text("UPDATE flagged_numbers SET fake_detection_count = :count WHERE id = :id"),
                        {"count": total_count, "id": latest_id},
                    )
                    other_ids = [r[0] for r in records[1:]]
                    for oid in other_ids:
                        conn.execute(text("DELETE FROM flagged_numbers WHERE id = :id"), {"id": oid})

            # 4. Ensure unique index on phone_number
            try:
                conn.execute(
                    text("CREATE UNIQUE INDEX IF NOT EXISTS uq_flagged_numbers_phone_number ON flagged_numbers (phone_number)")
                )
            except Exception:
                pass
    except Exception as exc:
        logger.warning("[Database] Scam number migration/consolidation check: %s", exc)


def init_db():
    """Create all tables in the database if they do not exist and ensure migrations are applied."""
    global engine, SessionLocal
    try:
        Base.metadata.create_all(bind=engine)
        _consolidate_and_migrate_flagged_numbers(engine)
    except Exception as e:
        # If running locally with a Render Internal Database URL (dpg-...), the host
        # only resolves inside Render's private network. Fall back to local SQLite.
        is_render = bool(os.environ.get("RENDER"))
        if not is_render and "dpg-" in DATABASE_URL and not ".render.com" in DATABASE_URL:
            logger.warning(
                f"[Database] Render internal hostname cannot be resolved from a local machine ({e}). "
                "Falling back to local SQLite ('sqlite:///./calling_app.db') for local development. "
                "To connect to Render PostgreSQL from your computer, use the 'External Database URL' from your Render dashboard."
            )
            fallback_url = "sqlite:///./calling_app.db"
            engine = create_engine(fallback_url, connect_args={"check_same_thread": False})
            SessionLocal.configure(bind=engine)
            Base.metadata.create_all(bind=engine)
            _consolidate_and_migrate_flagged_numbers(engine)
        else:
            raise e

