"""Account and session routes shared by TMX and the manager UI."""

import hashlib
import os
import re
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import bcrypt
import jwt
import psycopg
import redis
from psycopg.rows import dict_row


ACCESS_LIFETIME = timedelta(hours=4)
REFRESH_LIFETIME = timedelta(days=30)
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
SCHEMA_LOCK = threading.Lock()
SCHEMA_READY = False
DUMMY_HASH = bcrypt.hashpw(b"invalid-password", bcrypt.gensalt())
ROUTES = {
    "/auth/login", "/auth/complete-first-login", "/auth/select-provider",
    "/auth/refresh", "/auth/logout", "/auth/me",
    "/auth/me/last-selected-provider", "/auth/allusers",
    "/auth/admin-create-user", "/auth/admin-reset-password",
    "/auth/change-password", "/auth/remove",
}


class IdentityError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@contextmanager
def database():
    global SCHEMA_READY
    with psycopg.connect(
        host=os.environ.get("PG_HOST", "postgres"),
        port=int(os.environ.get("PG_PORT", "5432")),
        dbname=os.environ.get("PG_DATABASE", "tmx"),
        user=os.environ.get("PG_USER", "tmx"),
        password=os.environ["PG_PASSWORD"],
        row_factory=dict_row,
    ) as db:
        if not SCHEMA_READY:
            with SCHEMA_LOCK:
                if not SCHEMA_READY:
                    db.execute("""
                        CREATE TABLE IF NOT EXISTS manager_refresh_tokens (
                            token_id UUID PRIMARY KEY,
                            user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                            token_hash TEXT NOT NULL UNIQUE,
                            family_id UUID NOT NULL,
                            provider_id TEXT,
                            expires_at TIMESTAMPTZ NOT NULL,
                            revoked_at TIMESTAMPTZ,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                    """)
                    db.execute("CREATE INDEX IF NOT EXISTS manager_refresh_family ON manager_refresh_tokens(family_id)")
                    db.execute("CREATE INDEX IF NOT EXISTS manager_refresh_user ON manager_refresh_tokens(user_id)")
                    db.commit()
                    SCHEMA_READY = True
        yield db


def normalize_email(value):
    email = str(value or "").strip().lower()
    if not EMAIL.fullmatch(email):
        raise IdentityError("Enter a valid email")
    return email


def password_hash(value):
    if not isinstance(value, str) or len(value) < 8 or len(value.encode()) > 72:
        raise IdentityError("Password must contain at least 8 characters and at most 72 bytes")
    return bcrypt.hashpw(value.encode(), bcrypt.gensalt()).decode()


def get_user(db, email=None, user_id=None):
    if email is not None:
        return db.execute("""
            SELECT user_id, email, password, roles, permissions, data, must_change_password,
                   last_selected_provider_id, contact_email, email_verified_at
              FROM users WHERE lower(email) = %s
        """, (email,)).fetchone()
    return db.execute("""
        SELECT user_id, email, password, roles, permissions, data, must_change_password,
               last_selected_provider_id, contact_email, email_verified_at
          FROM users WHERE user_id = %s
    """, (user_id,)).fetchone()


def associations(db, user_id):
    return db.execute("""
        SELECT up.provider_id, up.provider_role, p.organisation_name,
               p.organisation_abbreviation
          FROM user_providers up
          LEFT JOIN providers p ON p.provider_id = up.provider_id
         WHERE up.user_id = %s ORDER BY up.created_at, up.provider_id
    """, (user_id,)).fetchall()


def provisioner_context(db, user):
    if "provisioner" not in (user["roles"] or []):
        return [], []
    ids = db.execute("SELECT provisioner_id FROM user_provisioners WHERE user_id = %s",
                     (user["user_id"],)).fetchall()
    provisioner_ids = [str(row["provisioner_id"]) for row in ids]
    providers = db.execute("""
        SELECT DISTINCT pp.provider_id, p.organisation_name, p.organisation_abbreviation
          FROM user_provisioners up
          JOIN provisioner_providers pp ON pp.provisioner_id = up.provisioner_id
          LEFT JOIN providers p ON p.provider_id = pp.provider_id
         WHERE up.user_id = %s
    """, (user["user_id"],)).fetchall()
    return provisioner_ids, [{"providerId": row["provider_id"],
                              "organisationName": row["organisation_name"],
                              "organisationAbbreviation": row["organisation_abbreviation"]}
                             for row in providers]


def signed(claims, lifetime=ACCESS_LIFETIME):
    now = datetime.now(timezone.utc)
    return jwt.encode({**claims, "iat": now, "exp": now + lifetime},
                      os.environ["JWT_SECRET"], algorithm="HS256")


def verified(token, audience="admin"):
    try:
        claims = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=["HS256"], audience=audience)
    except jwt.PyJWTError as exc:
        raise IdentityError("Invalid or expired token", 401) from exc
    return claims


def caller(db, token):
    if not token:
        raise IdentityError("Authentication required", 401)
    claims = verified(token)
    if claims.get("purpose") or not claims.get("email"):
        raise IdentityError("Authentication required", 401)
    user = get_user(db, email=claims["email"])
    if not user or str(user["user_id"]) != claims.get("userId"):
        raise IdentityError("Authentication required", 401)
    return user


def superadmin(db, token):
    user = caller(db, token)
    if "superadmin" not in (user["roles"] or []):
        raise IdentityError("Superadmin access required", 403)
    return user


def session_claims(db, user, chosen_provider=None):
    user_data = user["data"] or {}
    rows = associations(db, user["user_id"])
    provider_ids = [row["provider_id"] for row in rows]
    provisioner_ids, provisioner_providers = provisioner_context(db, user)
    managed_ids = [row["providerId"] for row in provisioner_providers]
    is_superadmin = "superadmin" in (user["roles"] or [])
    if chosen_provider and not is_superadmin and chosen_provider not in provider_ids + managed_ids:
        raise IdentityError("Not associated with that provider", 403)
    if not chosen_provider and not is_superadmin and len(provider_ids) == 1:
        chosen_provider = provider_ids[0]
    payload = {
        "sub": str(user["user_id"]), "userId": str(user["user_id"]),
        "email": user["email"], "roles": user["roles"] or [],
        "permissions": user["permissions"] or [], "services": user_data.get("services", []),
        "aud": "admin",
        "providerIds": provider_ids,
        "provisionerIds": provisioner_ids,
        "provisionerProviders": provisioner_providers,
        "providerAssociations": [{
            "providerId": row["provider_id"], "providerRole": row["provider_role"],
            "organisationName": row["organisation_name"],
            "organisationAbbreviation": row["organisation_abbreviation"],
        } for row in rows],
        "lastSelectedProviderId": user["last_selected_provider_id"],
    }
    if chosen_provider:
        payload["providerId"] = chosen_provider
        provider = db.execute("""
            SELECT data, organisation_abbreviation, organisation_name
              FROM providers WHERE provider_id = %s
        """, (chosen_provider,)).fetchone()
        if not provider:
            raise IdentityError("Provider not found", 404)
        payload["provider"] = {**(provider["data"] or {}), "organisationId": chosen_provider,
                               "organisationAbbreviation": provider["organisation_abbreviation"],
                               "organisationName": provider["organisation_name"]}
    return payload


def issue_session(db, user, chosen_provider=None, family_id=None):
    payload = session_claims(db, user, chosen_provider)
    if chosen_provider:
        payload["lastSelectedProviderId"] = chosen_provider
    refresh_token = secrets.token_urlsafe(48)
    db.execute("""
        INSERT INTO manager_refresh_tokens
          (token_id, user_id, token_hash, family_id, provider_id, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (uuid4(), user["user_id"], hashlib.sha256(refresh_token.encode()).hexdigest(),
          family_id or uuid4(), chosen_provider, datetime.now(timezone.utc) + REFRESH_LIFETIME))
    db.execute("UPDATE users SET last_access = NOW() WHERE user_id = %s", (user["user_id"],))
    return {"token": signed(payload), "refreshToken": refresh_token}


def login(db, data, client_ip=""):
    email = normalize_email(data.get("email"))
    password = data.get("password")
    if not isinstance(password, str):
        raise IdentityError("Invalid email or password", 401)
    # Redis provides a shared limit across Gunicorn workers and containers.
    limiter = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379"))
    for key in ("ip:" + client_ip, "email:" + hashlib.sha256(email.encode()).hexdigest()):
        full_key = "manager:login:" + key
        count = limiter.incr(full_key)
        if count == 1:
            limiter.expire(full_key, 60)
        if count > 10:
            raise IdentityError("Too many login attempts. Try again shortly.", 429)
    user = get_user(db, email=email)
    stored = (user or {}).get("password") or ""
    try:
        matched = bcrypt.checkpw(password.encode(), stored.encode() if stored.startswith("$2") else DUMMY_HASH)
    except ValueError:
        matched = False
    if not user or not matched or not stored.startswith("$2"):
        raise IdentityError("Invalid email or password", 401)
    if user["must_change_password"]:
        return {"mustChangePassword": True, "limitedToken": signed({
            "aud": "first-login", "email": email, "userId": str(user["user_id"]),
            "purpose": "first-login-password-change",
        }, timedelta(minutes=5))}
    rows = associations(db, user["user_id"])
    if "superadmin" not in (user["roles"] or []) and len(rows) > 1:
        return {
            "providerSelectionRequired": True,
            "providers": [{"providerId": row["provider_id"],
                           "providerRole": row["provider_role"],
                           "organisationName": row["organisation_name"],
                           "organisationAbbreviation": row["organisation_abbreviation"]} for row in rows],
            "lastSelectedProviderId": user["last_selected_provider_id"],
            "selectionToken": signed({"aud": "provider-selection", "purpose": "provider-selection",
                                      "selectingUserId": str(user["user_id"])}, timedelta(minutes=5)),
        }
    return issue_session(db, user)


def dispatch(path, data=None, token=None, method="POST", client_ip=""):
    data = data or {}
    with database() as db:
        if path == "/auth/login" and method == "POST":
            return login(db, data, client_ip)
        if path == "/auth/complete-first-login" and method == "POST":
            claims = verified(data.get("limitedToken", ""), "first-login")
            if claims.get("purpose") != "first-login-password-change":
                raise IdentityError("Invalid first-login token", 401)
            user = get_user(db, user_id=claims.get("userId"))
            if not user or not user["must_change_password"] or user["email"] != claims.get("email"):
                raise IdentityError("Invalid first-login token", 401)
            hashed = password_hash(data.get("newPassword"))
            changed = db.execute("""
                UPDATE users SET password = %s, must_change_password = FALSE, updated_at = NOW()
                 WHERE user_id = %s AND must_change_password = TRUE RETURNING user_id
            """, (hashed, user["user_id"])).fetchone()
            if not changed:
                raise IdentityError("Invalid first-login token", 401)
            return issue_session(db, user)
        if path == "/auth/select-provider" and method == "POST":
            try:
                claims = verified(token, "provider-selection")
                if claims.get("purpose") != "provider-selection":
                    raise IdentityError("Invalid selection token", 401)
                user = get_user(db, user_id=claims.get("selectingUserId"))
            except IdentityError:
                user = caller(db, token)
            if not user:
                raise IdentityError("Authentication required", 401)
            provider_id = data.get("providerId")
            if not isinstance(provider_id, str) or not provider_id:
                raise IdentityError("Select a provider")
            result = issue_session(db, user, provider_id)
            db.execute("UPDATE users SET last_selected_provider_id = %s WHERE user_id = %s",
                       (provider_id, user["user_id"]))
            return result
        if path == "/auth/refresh" and method == "POST":
            presented = data.get("refreshToken")
            if not isinstance(presented, str) or not presented:
                raise IdentityError("Invalid refresh token", 401)
            token_hash = hashlib.sha256(presented.encode()).hexdigest()
            row = db.execute("SELECT * FROM manager_refresh_tokens WHERE token_hash = %s FOR UPDATE", (token_hash,)).fetchone()
            if not row:
                raise IdentityError("Invalid refresh token", 401)
            if row["revoked_at"]:
                db.execute("UPDATE manager_refresh_tokens SET revoked_at = NOW() WHERE family_id = %s", (row["family_id"],))
                db.commit()
                raise IdentityError("Refresh token reuse detected", 401)
            if row["expires_at"] <= datetime.now(timezone.utc):
                raise IdentityError("Expired refresh token", 401)
            user = get_user(db, user_id=row["user_id"])
            if not user:
                raise IdentityError("Invalid refresh token", 401)
            db.execute("UPDATE manager_refresh_tokens SET revoked_at = NOW() WHERE token_id = %s", (row["token_id"],))
            return issue_session(db, user, row["provider_id"], row["family_id"])
        if path == "/auth/logout" and method == "POST":
            presented = data.get("refreshToken")
            if isinstance(presented, str) and presented:
                db.execute("UPDATE manager_refresh_tokens SET revoked_at = NOW() WHERE token_hash = %s",
                           (hashlib.sha256(presented.encode()).hexdigest(),))
            return {"success": True}
        if path == "/auth/me" and method == "GET":
            user = caller(db, token)
            rows = associations(db, user["user_id"])
            return {"userId": str(user["user_id"]), "email": user["email"],
                    "isSuperAdmin": "superadmin" in (user["roles"] or []),
                    "globalRoles": user["roles"] or [],
                    "providerRoles": {row["provider_id"]: row["provider_role"] for row in rows},
                    "providerIds": [row["provider_id"] for row in rows],
                    "provisionerProviderIds": [row["providerId"] for row in provisioner_context(db, user)[1]],
                    "contactEmail": user["contact_email"],
                    "emailVerifiedAt": user["email_verified_at"].isoformat() if user["email_verified_at"] else None}
        if path == "/auth/me/last-selected-provider" and method == "PATCH":
            user = caller(db, token)
            provider_id = data.get("providerId")
            allowed_ids = [row["provider_id"] for row in associations(db, user["user_id"])]
            allowed_ids.extend(row["providerId"] for row in provisioner_context(db, user)[1])
            if provider_id is not None and (not isinstance(provider_id, str) or
                    ("superadmin" not in (user["roles"] or []) and provider_id not in allowed_ids)):
                raise IdentityError("Not associated with that provider", 403)
            db.execute("UPDATE users SET last_selected_provider_id = %s WHERE user_id = %s",
                       (provider_id, user["user_id"]))
            return {"success": True}
        if path == "/auth/allusers" and method == "POST":
            superadmin(db, token)
            rows = db.execute("""
                SELECT u.user_id, u.email, u.roles, u.permissions, u.data,
                       COALESCE(array_agg(up.provider_id) FILTER (WHERE up.provider_id IS NOT NULL), ARRAY[]::text[]) AS provider_ids
                  FROM users u LEFT JOIN user_providers up ON up.user_id = u.user_id
                 GROUP BY u.user_id ORDER BY u.email
            """).fetchall()
            return {"success": True, "users": [{"key": row["email"], "value": {
                **(row["data"] or {}), "userId": str(row["user_id"]), "email": row["email"],
                "roles": row["roles"] or [], "permissions": row["permissions"] or [],
                "providerIds": row["provider_ids"],
            }} for row in rows]}
        if path == "/auth/admin-create-user" and method == "POST":
            superadmin(db, token)
            if data.get("roles", ["client"]) != ["client"]:
                raise IdentityError("Only client accounts are supported")
            email = normalize_email(data.get("email"))
            if get_user(db, email=email):
                raise IdentityError("User already exists", 409)
            password = secrets.token_urlsafe(18)
            db.execute("""
                INSERT INTO users (email, password, roles, permissions, must_change_password, data)
                VALUES (%s, %s, %s, '[]', TRUE, '{}')
            """, (email, password_hash(password), '["client"]'))
            return {"success": True, "email": email, "password": password}
        if path == "/auth/admin-reset-password" and method == "POST":
            superadmin(db, token)
            email = normalize_email(data.get("email"))
            user = get_user(db, email=email)
            if not user:
                raise IdentityError("User not found", 404)
            password = data.get("newPassword") or secrets.token_urlsafe(18)
            db.execute("UPDATE users SET password = %s, updated_at = NOW() WHERE user_id = %s",
                       (password_hash(password), user["user_id"]))
            db.execute("UPDATE manager_refresh_tokens SET revoked_at = NOW() WHERE user_id = %s AND revoked_at IS NULL",
                       (user["user_id"],))
            return {"success": True, "password": password}
        if path == "/auth/change-password" and method == "POST":
            user = caller(db, token)
            old = data.get("currentPassword")
            stored = user["password"] or ""
            if not isinstance(old, str) or not stored.startswith("$2") or not bcrypt.checkpw(old.encode(), stored.encode()):
                raise IdentityError("Invalid password", 401)
            db.execute("UPDATE users SET password = %s, updated_at = NOW() WHERE user_id = %s",
                       (password_hash(data.get("newPassword")), user["user_id"]))
            db.execute("UPDATE manager_refresh_tokens SET revoked_at = NOW() WHERE user_id = %s AND revoked_at IS NULL",
                       (user["user_id"],))
            return {"success": True}
        if path == "/auth/remove" and method == "POST":
            editor = superadmin(db, token)
            email = normalize_email(data.get("email"))
            if email == editor["email"]:
                raise IdentityError("You cannot remove your own account")
            db.execute("DELETE FROM users WHERE lower(email) = %s", (email,))
            return {"success": True}
    raise IdentityError("Not found", 404)
