import os
import random
import re
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from enum import Enum

import pyodbc
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Response, Request
from jose import jwt, JWTError, ExpiredSignatureError
from pydantic import BaseModel, EmailStr

load_dotenv()

# ─────────────────────────── Config ────────────────────────────
DRIVER_NAME = os.getenv("DRIVER_NAME")
SERVER_NAME = os.getenv("SERVER_NAME")
DB_NAME     = os.getenv("DB_NAME")
SECRET_KEY  = os.getenv("SECRET_KEY")
ALGORITHM   = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES"))

SMTP_HOST       = os.getenv("SMTP_HOST")
SMTP_PORT       = int(os.getenv("SMTP_PORT", 587))
SMTP_USER       = os.getenv("SMTP_USER")
SMTP_PASSWORD   = os.getenv("SMTP_PASSWORD")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL")

IS_ACTIVE = True

CONNECTION_STRING = (
    f"DRIVER={DRIVER_NAME};"
    f"SERVER={SERVER_NAME};"
    f"DATABASE={DB_NAME};"
    f"UID={os.getenv('DB_USER')};"
    f"PWD={os.getenv('DB_PASSWORD')};"
    f"TrustServerCertificate=yes;"
)

router = APIRouter(tags=["auth"])


# ─────────────────────────── DB helper ─────────────────────────
def get_connection():
    """Return a new pyodbc connection. Caller is responsible for closing it."""
    return pyodbc.connect(CONNECTION_STRING)


# ─────────────────────────── Named-parameter query helpers ─────
_NAMED_PARAM_RE = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")


def _compile_named_query(sql: str, params: dict):
    """
    Convert a SQL string that uses ':paramName' placeholders into pyodbc's
    required '?' placeholders, returning (converted_sql, ordered_values).
    Still fully parameterized under the hood — values are never interpolated
    into the SQL string.
    """
    values = []

    def _replace(match: "re.Match") -> str:
        name = match.group(1)
        if name not in params:
            raise KeyError(f"Missing SQL parameter: {name}")
        values.append(params[name])
        return "?"

    converted_sql = _NAMED_PARAM_RE.sub(_replace, sql)
    return converted_sql, values


def run_query(cursor, sql: str, **params):
    """Execute an INSERT/UPDATE/DELETE (or SELECT) using :name placeholders."""
    converted_sql, values = _compile_named_query(sql, params)
    cursor.execute(converted_sql, values)
    return cursor


def fetch_one(cursor, sql: str, **params):
    run_query(cursor, sql, **params)
    return cursor.fetchone()


def fetch_all(cursor, sql: str, **params):
    run_query(cursor, sql, **params)
    return cursor.fetchall()


# ─────────────────────────── JWT helpers ───────────────────────
def create_jwt_token(person_id: int, user_name: str):
    expiry = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"personId": person_id, "userName": user_name, "exp": expiry}
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return token, expiry


def decode_token(token: str) -> dict:
    """Decode and validate a JWT. Raises HTTPException on failure."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="expired token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def get_token_from_cookie(request: Request) -> str:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Access token missing")
    return token


def require_auth(request: Request) -> dict:
    """JWT + DB validation. Raises HTTPException(401) on any failure."""
    token = get_token_from_cookie(request)
    payload = decode_token(token)

    person_id = payload.get("personId")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        row = fetch_one(
            cursor,
            """
            SELECT ExpiredTime
            FROM   Newyork_AccessToken
            WHERE  UserId           = :personId
              AND  AccessTokenValue = :token
              AND  IsActive         = 1
            """,
            personId=person_id,
            token=token,
        )
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=401, detail="Token not found or revoked")
    if row[0] < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Token expired")

    return payload  # {"personId": ..., "userName": ..., "exp": ...}


# ─────────────────────────── Email helper ──────────────────────
def send_email(to_email: str, subject: str, body: str):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM_EMAIL
    msg["To"] = to_email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM_EMAIL, to_email, msg.as_string())


# ─────────────────────────── Enums / Schemas ───────────────────
class RoleEnum(str, Enum):
    User       = "User"
    Consultant = "Consultant"
    Lead       = "Lead"


class RegisterRequest(BaseModel):
    name:        str
    email:       EmailStr
    companyName: str
    mobile:      str
    country:     str
    address:     str
    role:        RoleEnum
    userName:    str
    password:    str


class LoginRequest(BaseModel):
    userName: str
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email:           EmailStr
    resetCode:       str
    newPassword:     str
    confirmPassword: str


# ══════════════════════════════════════════════════════════════
#  AUTH ENDPOINTS
# ══════════════════════════════════════════════════════════════

@router.post("/register")
def register_user(request: RegisterRequest, auth_request: Request):
    person_id = -1
    if not IS_ACTIVE:
        res = get_person_details(auth_request)
        if res["data"].get("role", "") != "Lead":
            raise HTTPException(status_code=401, detail="No Access To Register")
        person_id = res["data"].get("id", -1)

    conn = get_connection()
    cursor = conn.cursor()
    try:
        existing = fetch_one(
            cursor,
            "SELECT UserName, Email FROM Newyork_Person WHERE UserName = :userName OR Email = :email",
            userName=request.userName,
            email=request.email,
        )
        if existing:
            field = "Username" if existing[0] == request.userName else "Email"
            raise HTTPException(status_code=400, detail=f"{field} already exists")

        now = datetime.now()
        row = fetch_one(
            cursor,
            """
            INSERT INTO Newyork_Person
                (Name, Email, CompanyName, Mobile, Country, Address, Role,
                    UserName, Password, CreditsRemaining,
                    DateCreated, DateUpdated, IsActive, CreatedBy, UpdatedBy)
            OUTPUT INSERTED.Id
            VALUES (:name, :email, :companyName, :mobile, :country, :address, :role,
                    :userName, :password, 3, :now, :now, 1, :createdBy, :updatedBy)
            """,
            name=request.name,
            email=request.email,
            companyName=request.companyName,
            mobile=request.mobile,
            country=request.country,
            address=request.address,
            role=request.role.value,
            userName=request.userName,
            password=request.password,
            now=now,
            createdBy=person_id,
            updatedBy=person_id,
        )
        new_id = row[0]
        conn.commit()
    finally:
        conn.close()

    return {"status": 200, "message": "User registered successfully", "personId": new_id}


@router.post("/login")
def login_user(auth_request: Request, request: LoginRequest, response: Response):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        user = fetch_one(
            cursor,
            """
            SELECT Id, Name, Email, Role
            FROM   Newyork_Person
            WHERE  UserName = :userName AND Password = :password AND IsActive = 1
            """,
            userName=request.userName,
            password=request.password,
        )
        if not user:
            raise HTTPException(status_code=401, detail="Invalid username or password")

        person_id, name, email, role = user
        token, expiry = create_jwt_token(person_id, request.userName)
        now = datetime.now()

        # Delete old tokens for this user before creating a new one
        run_query(
            cursor,
            "DELETE FROM Newyork_AccessToken WHERE UserId = :personId",
            personId=person_id,
        )
        conn.commit()

        run_query(
            cursor,
            """
            INSERT INTO Newyork_AccessToken
            (
                UserId,
                AccessTokenValue,
                RefreshToken,
                ExpiredTime,
                DateCreated,
                DateUpdated,
                CreatedBy,
                UpdatedBy,
                IsActive
            )
            VALUES (:personId, :token, NULL, :expiry, :now, :now, :personId, :personId, 1);
            """,
            personId=person_id,
            token=token,
            expiry=expiry,
            now=now,
        )
        conn.commit()
    finally:
        conn.close()

    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        secure=True,
        samesite="none"
    )

    return {
        "status": 200,
        "message": "Login Success",
        "personId": person_id,
        "name": name,
        "email": email,
        "role": role
    }


@router.get("/refresh-token")
def refresh_token(request: Request, response: Response):
    payload = require_auth(request)
    person_id = payload["personId"]
    user_name = payload["userName"]

    token, expiry = create_jwt_token(person_id, user_name)
    now = datetime.now()

    conn = get_connection()
    cursor = conn.cursor()
    try:
        run_query(
            cursor,
            """
            UPDATE Newyork_AccessToken
            SET    AccessTokenValue = :newToken, ExpiredTime = :expiry, DateUpdated = :now
            WHERE  UserId = :personId AND AccessTokenValue = :oldToken AND IsActive = 1
            """,
            newToken=token,
            expiry=expiry,
            now=now,
            personId=person_id,
            oldToken=request.cookies.get("access_token"),
        )
        conn.commit()
    finally:
        conn.close()

    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        secure=True,
        samesite="none",
    )
    return {"status": 200, "message": "Token refreshed"}


@router.get("/person/details")
def get_person_details(request: Request):
    payload = require_auth(request)   # JWT + DB validation
    person_id = payload["personId"]

    conn = get_connection()
    cursor = conn.cursor()
    try:
        row = fetch_one(
            cursor,
            """
            SELECT Id, Name, Email, CompanyName, Mobile, Country, Address,
                   Role, UserName, CreditsRemaining,
                   DateCreated, DateUpdated, IsActive, CreatedBy, UpdatedBy
            FROM   Newyork_Person
            WHERE  Id = :personId AND IsActive = 1
            """,
            personId=person_id,
        )
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    (pid, name, email, company, mobile, country, address,
     role, username, credits, date_created, date_updated,
     is_active, created_by, updated_by) = row

    return {
        "status": 200,
        "data": {
            "id": pid, "name": name, "email": email,
            "companyName": company, "mobile": mobile,
            "country": country, "address": address, "role": role,
            "userName": username, "creditsRemaining": credits,
            "dateCreated": date_created, "dateUpdated": date_updated,
            "isActive": is_active, "createdBy": created_by, "updatedBy": updated_by
        }
    }


@router.post("/forgot-password")
def forgot_password(request: ForgotPasswordRequest):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        user = fetch_one(
            cursor,
            "SELECT Id, Name FROM Newyork_Person WHERE Email = :email AND IsActive = 1",
            email=request.email,
        )
        if not user:
            raise HTTPException(status_code=404, detail="Email not found")

        person_id, name = user
        reset_code = str(random.randint(100000, 999999))
        code_expiry = datetime.now() + timedelta(minutes=10)

        run_query(
            cursor,
            """
            UPDATE Newyork_Person
            SET    ResetCode = :resetCode, ResetCodeExpiry = :codeExpiry, DateUpdated = :now
            WHERE  Id = :personId
            """,
            resetCode=reset_code,
            codeExpiry=code_expiry,
            now=datetime.now(),
            personId=person_id,
        )
        conn.commit()
    finally:
        conn.close()

    send_email(
        to_email=request.email,
        subject="Password Reset Code",
        body=(
            f"Hi {name},\n\n"
            f"Your password reset code is: {reset_code}\n\n"
            f"This code is valid for 10 minutes.\n\nThanks,\nTicket System"
        )
    )
    return {"status": 200, "message": "Reset code sent to your email"}


@router.post("/reset-password")
def reset_password(request: ResetPasswordRequest):
    if request.newPassword != request.confirmPassword:
        raise HTTPException(status_code=400, detail="Passwords do not match")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        user = fetch_one(
            cursor,
            "SELECT Id, ResetCode, ResetCodeExpiry FROM Newyork_Person WHERE Email = :email AND IsActive = 1",
            email=request.email,
        )
        if not user:
            raise HTTPException(status_code=404, detail="Email not found")

        person_id, stored_code, code_expiry = user

        if stored_code != request.resetCode:
            raise HTTPException(status_code=400, detail="Invalid reset code")
        if code_expiry is None or code_expiry < datetime.now():
            raise HTTPException(status_code=400, detail="Reset code expired")

        run_query(
            cursor,
            """
            UPDATE Newyork_Person
            SET    Password = :password, ResetCode = NULL, ResetCodeExpiry = NULL,
                   DateUpdated = :now, UpdatedBy = :personId
            WHERE  Id = :personId
            """,
            password=request.newPassword,
            now=datetime.now(),
            personId=person_id,
        )
        conn.commit()
    finally:
        conn.close()

    return {"status": 200, "message": "Password reset successfully"}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("access_token")
    if token:
        conn = get_connection()
        cursor = conn.cursor()
        try:
            run_query(
                cursor,
                "UPDATE Newyork_AccessToken SET DateUpdated = :now WHERE AccessTokenValue = :token AND IsActive = 1",
                now=datetime.now(),
                token=token,
            )
            conn.commit()
        finally:
            conn.close()

    response.delete_cookie("access_token")
    return {"status": 200, "message": "Logout Success"}