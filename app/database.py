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


def init_db():
    """Create all tables in the database if they do not exist."""
    global engine, SessionLocal
    try:
        Base.metadata.create_all(bind=engine)
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
        else:
            raise e
