"""
routers/auth.py — registration and login
========================================
POST /auth/register-hospital   create an organisation + its admin account
POST /auth/register-ambulance  create an ambulance account, joining an org by code
POST /auth/login               email + password -> JWT
GET  /auth/me                  current user (+ org)
"""

import secrets
import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import auth
import db

router = APIRouter(prefix="/auth", tags=["auth"])


class HospitalRegister(BaseModel):
    org_name: str = Field(..., min_length=2, max_length=80)
    email: str = Field(..., min_length=5, max_length=120)
    password: str = Field(..., min_length=8, max_length=128)


class AmbulanceRegister(BaseModel):
    join_code: str = Field(..., min_length=4, max_length=32)
    unit_id: str = Field(..., min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    display_name: str = Field("", max_length=80)
    email: str = Field(..., min_length=5, max_length=120)
    password: str = Field(..., min_length=8, max_length=128)


class Login(BaseModel):
    email: str
    password: str


def _public(user, org_name: str) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "org_id": user["org_id"],
        "org_name": org_name,
        "unit_id": user["unit_id"],
        "display_name": user["display_name"],
    }


def _session(conn, user_id: int) -> dict:
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    org = conn.execute("SELECT name FROM orgs WHERE id = ?", (user["org_id"],)).fetchone()
    return {"token": auth.create_token(user_id), "user": _public(user, org["name"])}


def _check_email(email: str) -> str:
    email = email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(422, "Enter a valid email address")
    return email


@router.post("/register-hospital", status_code=201)
def register_hospital(req: HospitalRegister):
    email = _check_email(req.email)
    now = time.time()
    try:
        with db.connect() as conn:
            code = secrets.token_hex(3).upper()  # e.g. "A1B2C3"
            org_id = conn.execute(
                "INSERT INTO orgs (name, join_code, created_at) VALUES (?, ?, ?)",
                (req.org_name.strip(), code, now),
            ).lastrowid
            user_id = conn.execute(
                "INSERT INTO users (email, password_hash, role, org_id, display_name, created_at) "
                "VALUES (?, ?, 'hospital', ?, ?, ?)",
                (email, auth.hash_password(req.password), org_id, req.org_name.strip(), now),
            ).lastrowid
            return _session(conn, user_id)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "That email is already registered")


@router.post("/register-ambulance", status_code=201)
def register_ambulance(req: AmbulanceRegister):
    email = _check_email(req.email)
    with db.connect() as conn:
        org = conn.execute(
            "SELECT id FROM orgs WHERE join_code = ?", (req.join_code.strip().upper(),)
        ).fetchone()
        if org is None:
            raise HTTPException(404, "Invalid join code")
        try:
            user_id = conn.execute(
                "INSERT INTO users (email, password_hash, role, org_id, unit_id, display_name, created_at) "
                "VALUES (?, ?, 'ambulance', ?, ?, ?, ?)",
                (email, auth.hash_password(req.password), org["id"], req.unit_id,
                 req.display_name.strip() or req.unit_id, time.time()),
            ).lastrowid
        except sqlite3.IntegrityError as e:
            msg = "That unit ID is already taken" if "unit_id" in str(e) else "That email is already registered"
            raise HTTPException(409, msg)
        return _session(conn, user_id)


@router.post("/login")
def login(req: Login):
    with db.connect() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?", (req.email.strip().lower(),)
        ).fetchone()
        if user is None or not auth.verify_password(req.password, user["password_hash"]):
            raise HTTPException(401, "Incorrect email or password")
        return _session(conn, user["id"])


@router.get("/me")
def me(user=Depends(auth.current_user)):
    with db.connect() as conn:
        org = conn.execute("SELECT name FROM orgs WHERE id = ?", (user["org_id"],)).fetchone()
    return _public(user, org["name"])
