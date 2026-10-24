"""Google Calendar over OAuth 2.0 for the single owner. Credentials and tokens live in one 0600 file."""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

import requests

AUTH_URL = os.environ.get("GOOGLE_AUTH_URL", "https://accounts.google.com/o/oauth2/v2/auth")
TOKEN_URL = os.environ.get("GOOGLE_TOKEN_URL", "https://oauth2.googleapis.com/token")
REVOKE_URL = os.environ.get("GOOGLE_REVOKE_URL", "https://oauth2.googleapis.com/revoke")
API_URL = os.environ.get("GOOGLE_CALENDAR_API", "https://www.googleapis.com/calendar/v3").rstrip("/")
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
SCOPES = f"openid email {CALENDAR_SCOPE}"
STATE_TTL = 600
CLIENT_ID = re.compile(r"^[0-9]+-[0-9a-z]+\.apps\.googleusercontent\.com$")
TOKEN_FIELDS = ("refresh_token", "access_token", "expires_at", "email")


class GoogleError(Exception):
    """Message safe to show and log: never carries a token, code or secret."""


class Revoked(GoogleError):
    pass


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def id_token_email(token: str | None) -> str | None:
    # Received straight from Google's token endpoint over TLS, so the signature need not be checked (OIDC 3.1.3.7).
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return claims.get("email")
    except Exception:
        return None


def rfc3339(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def error_code(response: requests.Response) -> str:
    try:
        return str(response.json().get("error") or response.status_code)
    except ValueError:
        return str(response.status_code)


class Google:
    def __init__(self, path: Path, redirect_uri: str):
        self.path = path
        self.redirect_uri = redirect_uri
        self.lock = threading.RLock()
        self.pending: dict[str, tuple[str, str, float]] = {}

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as out:
            json.dump(data, out)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def update(self, **fields) -> dict:
        with self.lock:
            data = self.load()
            for key, value in fields.items():
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
            self.save(data)
            return data

    def configured(self) -> bool:
        data = self.load()
        return bool(data.get("client_id") and data.get("client_secret"))

    def connected(self) -> bool:
        return bool(self.load().get("refresh_token"))

    def email(self) -> str | None:
        return self.load().get("email")

    def client_id(self) -> str:
        return self.load().get("client_id", "")

    def set_credentials(self, client_id: str, client_secret: str) -> None:
        client_id, client_secret = client_id.strip(), client_secret.strip()
        if not CLIENT_ID.match(client_id):
            raise ValueError("Client ID non valido: finisce con .apps.googleusercontent.com.")
        if not client_secret or len(client_secret) > 200 or any(c.isspace() for c in client_secret):
            raise ValueError("Client secret non valido.")
        with self.lock:
            self.disconnect()
            self.save({"client_id": client_id, "client_secret": client_secret})

    def clear_credentials(self) -> None:
        with self.lock:
            self.disconnect()
            self.path.unlink(missing_ok=True)

    def authorize_url(self, session_key: str) -> str:
        if not self.configured():
            raise GoogleError("Configura prima Client ID e Client secret di Google.")
        now = time.time()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if v[2] > now}
            self.pending[state] = (hashlib.sha256(session_key.encode()).hexdigest(), verifier, now + STATE_TTL)
        params = {
            "client_id": self.client_id(),
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": SCOPES,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
            "code_challenge": b64url(hashlib.sha256(verifier.encode()).digest()),
            "code_challenge_method": "S256",
        }
        return f"{AUTH_URL}?{urlencode(params, quote_via=quote)}"

    def finish(self, state: str, code: str, session_key: str) -> str:
        """Swaps the authorization code for tokens; returns the connected account's email."""
        with self.lock:
            entry = self.pending.pop(state or "", None)
        if not entry or entry[2] < time.time():
            raise GoogleError("Richiesta scaduta o non valida: riprova da Collega Google Calendar.")
        if not hmac.compare_digest(entry[0], hashlib.sha256(session_key.encode()).hexdigest()):
            raise GoogleError("Il collegamento è partito da un'altra sessione: riprova.")
        if not code:
            raise GoogleError("Google non ha restituito il codice di autorizzazione.")
        data = self.load()
        response = requests.post(TOKEN_URL, data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri,
            "client_id": data.get("client_id"), "client_secret": data.get("client_secret"), "code_verifier": entry[1],
        }, timeout=30)
        if response.status_code != 200:
            raise GoogleError(f"Google ha rifiutato il collegamento ({error_code(response)}).")
        tokens = response.json()
        if CALENDAR_SCOPE not in (tokens.get("scope") or "").split():
            self.revoke(tokens.get("refresh_token") or tokens.get("access_token"))
            raise GoogleError("Serve il permesso di vedere gli eventi del calendario: ricollega e lascia spuntata la casella del calendario.")
        if not tokens.get("refresh_token"):
            raise GoogleError("Google non ha concesso l'accesso permanente: scollega l'app da myaccount.google.com e riprova.")
        email = id_token_email(tokens.get("id_token")) or ""
        self.update(refresh_token=tokens["refresh_token"], access_token=tokens.get("access_token"),
                    expires_at=time.time() + float(tokens.get("expires_in") or 0), email=email)
        return email

    def access_token(self, force: bool = False) -> str:
        with self.lock:
            data = self.load()
            if not data.get("refresh_token"):
                raise Revoked("Google Calendar non è collegato.")
            if not force and data.get("access_token") and data.get("expires_at", 0) - 60 > time.time():
                return data["access_token"]
            response = requests.post(TOKEN_URL, data={
                "grant_type": "refresh_token", "refresh_token": data["refresh_token"],
                "client_id": data.get("client_id"), "client_secret": data.get("client_secret"),
            }, timeout=30)
            if response.status_code != 200:
                code = error_code(response)
                if code in ("invalid_grant", "unauthorized_client", "invalid_client"):
                    self.update(**{field: None for field in TOKEN_FIELDS})
                    raise Revoked("Google ha revocato l'accesso al calendario: ricollega l'account.")
                raise GoogleError(f"Google non rinnova l'accesso ({code}).")
            tokens = response.json()
            self.update(access_token=tokens["access_token"], expires_at=time.time() + float(tokens.get("expires_in") or 3600))
            return tokens["access_token"]

    def list_events(self, time_min: float, time_max: float) -> list[dict]:
        params = {"singleEvents": "true", "orderBy": "startTime", "timeMin": rfc3339(time_min),
                  "timeMax": rfc3339(time_max), "maxResults": "250"}
        items: list[dict] = []
        retried = False
        while True:
            response = requests.get(f"{API_URL}/calendars/primary/events", params=params,
                                    headers={"Authorization": f"Bearer {self.access_token(force=retried)}"}, timeout=30)
            if response.status_code == 401 and not retried:
                retried = True
                continue
            if response.status_code != 200:
                raise GoogleError(f"Google Calendar ha risposto HTTP {response.status_code}.")
            body = response.json()
            items += body.get("items") or []
            if not body.get("nextPageToken") or len(items) > 2000:
                return items
            params["pageToken"] = body["nextPageToken"]

    def revoke(self, token: str | None) -> bool:
        if not token:
            return True
        try:
            return requests.post(REVOKE_URL, data={"token": token}, timeout=15).status_code in (200, 400)
        except requests.RequestException:
            return False

    def disconnect(self) -> bool:
        """Revokes at Google (best effort) and always forgets the tokens locally."""
        with self.lock:
            data = self.load()
            ok = self.revoke(data.get("refresh_token") or data.get("access_token"))
            if any(field in data for field in TOKEN_FIELDS):
                self.update(**{field: None for field in TOKEN_FIELDS})
            return ok
