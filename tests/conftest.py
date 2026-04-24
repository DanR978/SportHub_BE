"""
Pytest fixtures: SQLite in-memory database, FastAPI test client, authenticated
user factories. The scheduler and rate limiter are disabled here so tests are
fast and deterministic.

Important: this file imports `database` and replaces its engine/SessionLocal
before any app code creates tables, so all models are created in the test DB.
"""
import os

os.environ.setdefault("SECRET_KEY", "test-secret-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ["DISABLE_SCHEDULER"] = "true"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database

# Replace the production engine with an in-memory SQLite one before anything else.
_test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(_test_engine, "connect")
def _enable_fk(dbapi_connection, _):
    # SQLite has foreign keys off by default; tests need them on.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


_TestSessionLocal = sessionmaker(bind=_test_engine, autocommit=False, autoflush=False)
database.engine = _test_engine
database.SessionLocal = _TestSessionLocal


def _override_get_db():
    db = _TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


database.get_db = _override_get_db

import models  # noqa: F401 — register all models on Base.metadata
from auth import hash_password, issue_token_pair
from database import Base, get_db
from main import app
from models.db_user import DBUser
from rate_limiter import limiter

app.dependency_overrides[get_db] = _override_get_db

# Rate limiting would flake cross-test; disable globally.
limiter.enabled = False


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(bind=_test_engine)
    Base.metadata.create_all(bind=_test_engine)
    yield


@pytest.fixture
def db():
    session = _TestSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    # Suppress lifespan so the scheduler doesn't try to start.
    with TestClient(app) as c:
        yield c


def _make_user(db, email: str, is_admin: bool = False, first_name: str = "Test", last_name: str = "User") -> DBUser:
    user = DBUser(
        email=email,
        first_name=first_name,
        last_name=last_name,
        hashed_password=hash_password("password123"),
        is_admin=is_admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def user(db):
    return _make_user(db, "alice@example.com")


@pytest.fixture
def other_user(db):
    return _make_user(db, "bob@example.com", first_name="Bob", last_name="Smith")


@pytest.fixture
def admin_user(db):
    return _make_user(db, "admin@example.com", is_admin=True, first_name="Admin", last_name="One")


@pytest.fixture
def auth_headers(user):
    tokens = issue_token_pair(user.email)
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
def other_auth_headers(other_user):
    tokens = issue_token_pair(other_user.email)
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
def admin_auth_headers(admin_user):
    tokens = issue_token_pair(admin_user.email)
    return {"Authorization": f"Bearer {tokens['access_token']}"}
