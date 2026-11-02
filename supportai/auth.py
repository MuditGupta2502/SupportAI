"""OIDC SSO and local demo authentication with server-side sessions."""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from dotenv import load_dotenv

load_dotenv(override=False)


from supportai.data_paths import DATA_DIR

AUTH_DB = DATA_DIR / "auth.sqlite3"
SESSION_COOKIE = "supportai_session"
OIDC_FLOW_COOKIE = "supportai_oidc_flow"
SESSION_TTL_SECONDS = 8 * 60 * 60
LOGIN_TTL_SECONDS = 5 * 60
PASSWORD_ITERATIONS = 600_000
DUMMY_PASSWORD_SALT = b"supportai-dummy-salt"
DUMMY_PASSWORD_HASH = hashlib.pbkdf2_hmac(
    "sha256", b"not-the-password", DUMMY_PASSWORD_SALT, PASSWORD_ITERATIONS
)


class _ConnectionAdapter:
    """Give SQLite and psycopg a compatible execute/context-manager surface."""

    def __init__(self, connection, *, postgres: bool):
        self.connection = connection
        self.postgres = postgres

    def execute(self, sql: str, params=()):
        if self.postgres:
            sql = sql.replace("?", "%s")
        return self.connection.execute(sql, params)

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self.connection.__exit__(exc_type, exc, traceback)


class AuthenticationError(ValueError):
    """Raised when an authentication flow cannot be completed safely."""


class AuthManager:
    """Small OIDC relying-party implementation for this single-process demo."""

    def __init__(self, database: Path = AUTH_DB):
        self.database = database
        self.database_url = os.getenv("DATABASE_URL", "").strip()
        if os.getenv("SUPPORTAI_REQUIRE_DATABASE", "").strip().lower() in {"1", "true", "yes"} and not self.database_url:
            raise RuntimeError("DATABASE_URL is required for this hosted deployment; refusing to use ephemeral local auth storage.")
        self.mode = os.getenv("SUPPORTAI_AUTH_MODE", "dev").strip().lower()
        if self.mode not in {"dev", "password", "oidc"}:
            raise RuntimeError("SUPPORTAI_AUTH_MODE must be 'dev', 'password', or 'oidc'.")
        self.issuer = os.getenv("SUPPORTAI_OIDC_ISSUER", "").strip()
        self.client_id = os.getenv("SUPPORTAI_OIDC_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("SUPPORTAI_OIDC_CLIENT_SECRET", "").strip()
        self.redirect_uri = os.getenv(
            "SUPPORTAI_OIDC_REDIRECT_URI", "http://127.0.0.1:8000/auth/callback"
        ).strip()
        self.admin_emails = {
            value.strip().casefold()
            for value in os.getenv("SUPPORTAI_OIDC_ADMIN_EMAILS", "").split(",")
            if value.strip()
        }
        self.admin_groups = {
            value.strip()
            for value in os.getenv("SUPPORTAI_OIDC_ADMIN_GROUPS", "").split(",")
            if value.strip()
        }
        self.groups_claim = os.getenv("SUPPORTAI_OIDC_GROUPS_CLAIM", "groups").strip() or "groups"
        self.cookie_secure = os.getenv("SUPPORTAI_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"}
        self._login_attempts: dict[str, list[float]] = {}
        self._login_lock = threading.Lock()
        if self.mode == "oidc":
            self._validate_oidc_settings()
        self._initialize_database()

    def _validate_oidc_settings(self) -> None:
        if not self.issuer or not self.client_id:
            raise RuntimeError("OIDC mode requires SUPPORTAI_OIDC_ISSUER and SUPPORTAI_OIDC_CLIENT_ID.")
        issuer_url = urlparse(self.issuer)
        callback_url = urlparse(self.redirect_uri)
        if issuer_url.scheme != "https" and issuer_url.hostname not in {"localhost", "127.0.0.1"}:
            raise RuntimeError("OIDC issuer must use HTTPS except for localhost development providers.")
        if callback_url.scheme != "https" and callback_url.hostname not in {"localhost", "127.0.0.1"}:
            raise RuntimeError("OIDC redirect URI must use HTTPS outside localhost development.")
        if callback_url.path != "/auth/callback" or callback_url.query or callback_url.fragment:
            raise RuntimeError("SUPPORTAI_OIDC_REDIRECT_URI must be the callback URL ending in /auth/callback, without query or fragment.")
        if callback_url.scheme == "https" and not self.cookie_secure:
            raise RuntimeError("Set SUPPORTAI_COOKIE_SECURE=true when using HTTPS.")

    def _connect(self):
        if self.database_url:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as error:
                raise RuntimeError("Install psycopg[binary] to use DATABASE_URL.") from error
            connection = psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=10)
            return _ConnectionAdapter(connection, postgres=True)
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        return _ConnectionAdapter(connection, postgres=False)

    def _initialize_database(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            if self.database_url:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS auth_sessions ("
                    "token_hash TEXT PRIMARY KEY, subject TEXT NOT NULL, email TEXT NOT NULL, "
                    "name TEXT NOT NULL, role TEXT NOT NULL, csrf_token TEXT NOT NULL, expires_at BIGINT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS oidc_logins ("
                    "state_hash TEXT PRIMARY KEY, nonce TEXT NOT NULL, verifier TEXT NOT NULL, "
                    "browser_hash TEXT NOT NULL DEFAULT '', expires_at BIGINT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS auth_users ("
                    "email TEXT PRIMARY KEY, subject TEXT NOT NULL UNIQUE, name TEXT NOT NULL, "
                    "role TEXT NOT NULL CHECK(role IN ('customer', 'admin')), salt BYTEA NOT NULL, "
                    "password_hash BYTEA NOT NULL, created_at BIGINT NOT NULL)"
                )
            else:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS auth_sessions ("
                    "token_hash TEXT PRIMARY KEY, subject TEXT NOT NULL, email TEXT NOT NULL, "
                    "name TEXT NOT NULL, role TEXT NOT NULL, csrf_token TEXT NOT NULL, expires_at INTEGER NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS oidc_logins ("
                    "state_hash TEXT PRIMARY KEY, nonce TEXT NOT NULL, verifier TEXT NOT NULL, "
                    "browser_hash TEXT NOT NULL DEFAULT '', expires_at INTEGER NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS auth_users ("
                    "email TEXT PRIMARY KEY, subject TEXT NOT NULL UNIQUE, name TEXT NOT NULL, "
                    "role TEXT NOT NULL CHECK(role IN ('customer', 'admin')), salt BLOB NOT NULL, "
                    "password_hash BLOB NOT NULL, created_at INTEGER NOT NULL)"
                )
                columns = {row[1] for row in connection.execute("PRAGMA table_info(oidc_logins)")}
                if "browser_hash" not in columns:
                    connection.execute("ALTER TABLE oidc_logins ADD COLUMN browser_hash TEXT NOT NULL DEFAULT ''")
            connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (int(time.time()),))
            # Revoke pre-account demo sessions so old role-toggle cookies cannot bypass login.
            connection.execute("DELETE FROM auth_sessions WHERE subject LIKE 'local-demo:%'")
            connection.execute("DELETE FROM oidc_logins WHERE expires_at <= ?", (int(time.time()),))

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _http_json(url: str, *, data: bytes | None = None, headers: dict | None = None) -> dict:
        request = Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
        try:
            with urlopen(request, timeout=12) as response:
                payload = json.loads(response.read(1_000_000))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise AuthenticationError("Identity provider request failed.") from error
        if not isinstance(payload, dict):
            raise AuthenticationError("Identity provider returned an invalid response.")
        return payload

    def _discovery(self) -> dict:
        metadata = self._http_json(f"{self.issuer.rstrip('/')}/.well-known/openid-configuration")
        if metadata.get("issuer", "") != self.issuer:
            raise AuthenticationError("Identity provider issuer does not match the configured issuer.")
        for required in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(metadata.get(required), str):
                raise AuthenticationError(f"Identity provider discovery is missing {required}.")
            endpoint = urlparse(metadata[required])
            if endpoint.scheme != "https" and endpoint.hostname not in {"localhost", "127.0.0.1"}:
                raise AuthenticationError("Identity provider endpoints must use HTTPS outside localhost.")
        return metadata

    def begin_oidc_login(self) -> tuple[str, str]:
        metadata = self._discovery()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        browser_flow = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO oidc_logins(state_hash, nonce, verifier, browser_hash, expires_at) VALUES (?, ?, ?, ?, ?)",
                (self._hash(state), nonce, verifier, self._hash(browser_flow), int(time.time()) + LOGIN_TTL_SECONDS),
            )
        query = urlencode({
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        separator = "&" if "?" in metadata["authorization_endpoint"] else "?"
        secure = "; Secure" if self.cookie_secure else ""
        flow_cookie = f"{OIDC_FLOW_COOKIE}={browser_flow}; Path=/auth; HttpOnly; SameSite=Lax; Max-Age={LOGIN_TTL_SECONDS}{secure}"
        return f"{metadata['authorization_endpoint']}{separator}{query}", flow_cookie

    def _consume_login(self, state: str, browser_flow: str) -> sqlite3.Row:
        state_hash = self._hash(state)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT nonce, verifier, browser_hash, expires_at FROM oidc_logins WHERE state_hash = ?", (state_hash,)
            ).fetchone()
            connection.execute("DELETE FROM oidc_logins WHERE state_hash = ?", (state_hash,))
        if (row is None or row["expires_at"] <= int(time.time()) or not browser_flow
                or not secrets.compare_digest(row["browser_hash"], self._hash(browser_flow))):
            raise AuthenticationError("Sign-in state is invalid or expired. Start sign-in again.")
        return row

    def _exchange_code(self, code: str, verifier: str, metadata: dict) -> dict:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": verifier,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        supported = metadata.get("token_endpoint_auth_methods_supported", ["client_secret_basic"])
        if self.client_secret and "client_secret_basic" in supported:
            pair = f"{quote_plus(self.client_id)}:{quote_plus(self.client_secret)}".encode("utf-8")
            headers["Authorization"] = "Basic " + base64.b64encode(pair).decode("ascii")
        elif self.client_secret and "client_secret_post" in supported:
            form["client_secret"] = self.client_secret
        elif self.client_secret and "none" not in supported:
            raise AuthenticationError("Identity provider has no supported token endpoint client authentication method.")
        elif not self.client_secret and supported and not {"none", "client_secret_basic", "client_secret_post"} & set(supported):
            raise AuthenticationError("Identity provider has no supported token endpoint client authentication method.")
        token = self._http_json(
            metadata["token_endpoint"],
            data=urlencode(form).encode("ascii"),
            headers=headers,
        )
        if not isinstance(token.get("id_token"), str):
            raise AuthenticationError("Identity provider did not return an ID token.")
        return token

    def _verify_id_token(self, token: str, nonce: str, metadata: dict) -> dict:
        try:
            from authlib.jose import JsonWebToken

            verifier = JsonWebToken(["RS256", "ES256", "PS256"])
            claims = verifier.decode(token, self._http_json(metadata["jwks_uri"]))
            claims.validate()
        except ImportError as error:
            raise AuthenticationError("Authlib is not installed in this environment.") from error
        except Exception as error:
            raise AuthenticationError("Identity provider ID token validation failed.") from error
        now = int(time.time())
        audience = claims.get("aud", [])
        audience = [audience] if isinstance(audience, str) else audience
        if claims.get("iss", "") != self.issuer:
            raise AuthenticationError("ID token issuer did not match the configured issuer.")
        if self.client_id not in audience:
            raise AuthenticationError("ID token audience did not match this application.")
        if len(audience) > 1 and claims.get("azp") != self.client_id:
            raise AuthenticationError("ID token authorized party did not match this application.")
        if claims.get("azp") is not None and claims.get("azp") != self.client_id:
            raise AuthenticationError("ID token authorized party did not match this application.")
        if claims.get("nonce") != nonce:
            raise AuthenticationError("ID token nonce did not match the sign-in request.")
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise AuthenticationError("ID token is missing a subject.")
        if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] <= now:
            raise AuthenticationError("ID token is expired.")
        if not isinstance(claims.get("iat"), (int, float)) or claims["iat"] > now + 60:
            raise AuthenticationError("ID token issued-at claim is invalid.")
        return dict(claims)

    def finish_oidc_login(self, state: str, code: str, browser_flow: str) -> tuple[str, dict]:
        transaction = self._consume_login(state, browser_flow)
        metadata = self._discovery()
        token = self._exchange_code(code, transaction["verifier"], metadata)
        claims = self._verify_id_token(token["id_token"], transaction["nonce"], metadata)
        email = str(claims.get("email", ""))
        email_verified = claims.get("email_verified") is True
        raw_groups = claims.get(self.groups_claim, [])
        groups = {raw_groups} if isinstance(raw_groups, str) else {value for value in raw_groups if isinstance(value, str)} if isinstance(raw_groups, list) else set()
        is_admin = bool(groups & self.admin_groups) or (email_verified and email.casefold() in self.admin_emails)
        user = {
            "sub": claims["sub"],
            "email": email,
            "name": str(claims.get("name") or email or "Support user")[:160],
            "role": "admin" if is_admin else "customer",
        }
        return self._create_session(user)

    @staticmethod
    def _normalize_email(email: str) -> str:
        email = email.strip().casefold()
        if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise AuthenticationError("Enter a valid email address.")
        return email

    @staticmethod
    def _password_hash(password: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)

    def _create_local_account(self, email: str, name: str, password: str, role: str) -> None:
        email = self._normalize_email(email)
        name = name.strip()
        if not name or len(name) > 120:
            raise AuthenticationError("Name must be between 1 and 120 characters.")
        if not isinstance(password, str) or len(password) < 12 or len(password) > 256:
            raise AuthenticationError("Password must be between 12 and 256 characters.")
        salt = secrets.token_bytes(16)
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO auth_users(email, subject, name, role, salt, password_hash, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (email, f"local:{uuid4()}", name, role, salt, self._password_hash(password, salt), int(time.time())),
                )
        except Exception as error:
            if isinstance(error, sqlite3.IntegrityError) or type(error).__name__ == "UniqueViolation":
                raise AuthenticationError("An account with this email already exists.") from error
            raise

    def register_customer(self, email: str, name: str, password: str) -> tuple[str, dict]:
        """Self-service registration always creates a customer account."""
        if self.mode not in {"dev", "password"}:
            raise AuthenticationError("Password registration is disabled; use organization SSO.")
        self._create_local_account(email, name, password, "customer")
        return self.login_local(email, password, expected_role="customer")

    def create_local_admin(self, email: str, name: str, password: str) -> None:
        """Provision an admin only through the local management command."""
        if self.mode not in {"dev", "password"}:
            raise AuthenticationError("Local admin accounts can only be created in password-auth mode.")
        self._create_local_account(email, name, password, "admin")

    def login_local(self, email: str, password: str, *, expected_role: str, client_ip: str = "") -> tuple[str, dict]:
        if self.mode not in {"dev", "password"}:
            raise AuthenticationError("Password sign-in is disabled; use organization SSO.")
        if expected_role not in {"customer", "admin"}:
            raise AuthenticationError("Choose customer or admin sign-in.")
        if not isinstance(password, str) or not password or len(password) > 256:
            raise AuthenticationError("Email or password is incorrect for this sign-in option.")
        email = self._normalize_email(email)
        attempt_key = self._hash(f"{email}|{client_ip}")
        now = time.time()
        with self._login_lock:
            attempts = [stamp for stamp in self._login_attempts.get(attempt_key, []) if now - stamp < 900]
            self._login_attempts[attempt_key] = attempts
            if len(attempts) >= 8:
                raise AuthenticationError("Too many sign-in attempts. Wait 15 minutes and try again.")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT subject, email, name, role, salt, password_hash FROM auth_users WHERE email = ?", (email,)
            ).fetchone()
        salt = row["salt"] if row else DUMMY_PASSWORD_SALT
        stored_hash = row["password_hash"] if row else DUMMY_PASSWORD_HASH
        submitted_hash = self._password_hash(password if isinstance(password, str) else "", salt)
        valid = hmac.compare_digest(submitted_hash, stored_hash)
        if not row or not valid or row["role"] != expected_role:
            with self._login_lock:
                self._login_attempts[attempt_key].append(now)
            raise AuthenticationError("Email or password is incorrect for this sign-in option.")
        with self._login_lock:
            self._login_attempts.pop(attempt_key, None)
        return self._create_session({
            "sub": row["subject"], "email": row["email"], "name": row["name"], "role": row["role"],
        })

    def _create_session(self, user: dict) -> tuple[str, dict]:
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        user = {**user, "csrf_token": csrf_token}
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO auth_sessions(token_hash, subject, email, name, role, csrf_token, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (self._hash(session_id), user["sub"], user["email"], user["name"], user["role"], csrf_token, int(time.time()) + SESSION_TTL_SECONDS),
            )
        return session_id, user

    @staticmethod
    def _cookie_value(cookie_header: str) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header or "")
            morsel = cookie.get(SESSION_COOKIE)
            return morsel.value if morsel else ""
        except Exception:
            return ""

    @staticmethod
    def flow_cookie_value(cookie_header: str) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header or "")
            morsel = cookie.get(OIDC_FLOW_COOKIE)
            return morsel.value if morsel else ""
        except Exception:
            return ""

    def get_session(self, cookie_header: str) -> dict | None:
        session_id = self._cookie_value(cookie_header)
        if not session_id:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT subject, email, name, role, csrf_token, expires_at FROM auth_sessions WHERE token_hash = ?",
                (self._hash(session_id),),
            ).fetchone()
            if row and row["expires_at"] <= int(time.time()):
                connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (self._hash(session_id),))
                return None
        if row is None:
            return None
        return {"sub": row["subject"], "email": row["email"], "name": row["name"], "role": row["role"], "csrf_token": row["csrf_token"]}

    def csrf_valid(self, cookie_header: str, submitted: str) -> bool:
        user = self.get_session(cookie_header)
        return bool(user and submitted and secrets.compare_digest(user["csrf_token"], submitted))

    def logout(self, cookie_header: str) -> None:
        session_id = self._cookie_value(cookie_header)
        if session_id:
            with self._connect() as connection:
                connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (self._hash(session_id),))

    def set_cookie_header(self, session_id: str) -> str:
        secure = "; Secure" if self.cookie_secure else ""
        return f"{SESSION_COOKIE}={session_id}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL_SECONDS}{secure}"

    def clear_cookie_header(self) -> str:
        secure = "; Secure" if self.cookie_secure else ""
        return f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0{secure}"

    def clear_flow_cookie_header(self) -> str:
        secure = "; Secure" if self.cookie_secure else ""
        return f"{OIDC_FLOW_COOKIE}=; Path=/auth; HttpOnly; SameSite=Lax; Max-Age=0{secure}"
