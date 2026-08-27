"""Gmail API client for one account.

Which account it serves comes from GMAIL_CREDENTIALS_DIR, so the same code runs
once per mailbox rather than juggling several inside one process. Keeping them
apart is the point: a draft cannot land in the wrong mailbox if the process
only ever holds one set of credentials.
"""

import base64
import json
import logging
import os
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# Google's access tokens last an hour. Refresh a little early so a call never
# fails on a token that expired mid-flight.
TOKEN_SKEW_SECONDS = 120


class GmailError(Exception):
    """Raised when a Gmail API call fails."""


class GmailClient:
    """Talks to one Gmail mailbox using a stored refresh token."""

    def __init__(self, credentials_dir: str | Path | None = None) -> None:
        directory = credentials_dir or os.getenv("GMAIL_CREDENTIALS_DIR")
        if not directory:
            raise GmailError("GMAIL_CREDENTIALS_DIR is not set")

        self._dir = Path(directory)
        self._creds = self._read_json("credentials.json")
        keys = self._read_json("gcp-oauth.keys.json")
        app = keys.get("installed") or keys.get("web") or {}
        self._client_id = app.get("client_id")
        self._client_secret = app.get("client_secret")

        if not (self._client_id and self._client_secret):
            raise GmailError(f"No OAuth client id/secret in {self._dir}")
        if not self._creds.get("refresh_token"):
            raise GmailError(f"No refresh token in {self._dir}")

        self._token: str | None = None
        self._token_expires_at = 0.0
        self._http = httpx.Client(timeout=30.0)

    def _read_json(self, name: str) -> dict:
        path = self._dir / name
        try:
            return json.loads(path.read_text())
        except OSError as exc:
            raise GmailError(f"Cannot read {path}: {exc}") from exc

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - TOKEN_SKEW_SECONDS:
            return self._token

        res = self._http.post(
            TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._creds["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
        if res.status_code != 200:
            raise GmailError(f"Token refresh failed: {res.text[:200]}")

        payload = res.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + float(payload.get("expires_in", 3600))
        logger.info("Refreshed Gmail access token")
        return self._token

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        body: Any = None,
    ) -> Any:
        res = self._http.request(
            method,
            f"{GMAIL_API}{path}",
            headers={"Authorization": f"Bearer {self._access_token()}"},
            params={k: v for k, v in (params or {}).items() if v is not None},
            json=body,
        )

        if res.status_code == 401:
            self._token = None
            res = self._http.request(
                method,
                f"{GMAIL_API}{path}",
                headers={"Authorization": f"Bearer {self._access_token()}"},
                params={k: v for k, v in (params or {}).items() if v is not None},
                json=body,
            )

        if res.status_code >= 400:
            raise GmailError(f"Gmail API {res.status_code}: {res.text[:300]}")

        return {} if res.status_code == 204 or not res.content else res.json()

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def profile(self) -> dict:
        """Address and message counts for the mailbox this server serves."""
        return self._request("GET", "/profile")

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    @staticmethod
    def _header(message: dict, name: str) -> str | None:
        headers = (message.get("payload") or {}).get("headers") or []
        for header in headers:
            if header.get("name", "").lower() == name.lower():
                return header.get("value")
        return None

    @staticmethod
    def _decode(data: str | None) -> str:
        if not data:
            return ""
        padded = data + "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")

    def _body_text(self, payload: dict) -> str:
        """Prefer text/plain, fall back to HTML, walking nested parts."""
        if not payload:
            return ""

        mime = payload.get("mimeType", "")
        if mime == "text/plain":
            return self._decode((payload.get("body") or {}).get("data"))

        parts = payload.get("parts") or []
        for part in parts:
            if part.get("mimeType") == "text/plain":
                return self._decode((part.get("body") or {}).get("data"))

        for part in parts:
            found = self._body_text(part)
            if found:
                return found

        if mime == "text/html":
            return self._decode((payload.get("body") or {}).get("data"))

        return ""

    def summarise(self, message: dict, include_body: bool = False) -> dict:
        """Flatten a Gmail message into the fields worth reading."""
        summary = {
            "id": message.get("id"),
            "thread_id": message.get("threadId"),
            "from": self._header(message, "From"),
            "to": self._header(message, "To"),
            "cc": self._header(message, "Cc"),
            "subject": self._header(message, "Subject"),
            "date": self._header(message, "Date"),
            "snippet": message.get("snippet"),
            "labels": message.get("labelIds", []),
            "unread": "UNREAD" in (message.get("labelIds") or []),
        }
        if include_body:
            summary["body"] = self._body_text(message.get("payload") or {})
        return summary

    def search(self, query: str, limit: int = 20, include_spam: bool = False) -> list[dict]:
        """Search the mailbox with Gmail's own query syntax."""
        listing = self._request(
            "GET",
            "/messages",
            params={
                "q": query or None,
                "maxResults": min(limit, 100),
                "includeSpamTrash": "true" if include_spam else None,
            },
        )
        out = []
        for stub in listing.get("messages", []) or []:
            full = self._request(
                "GET", f"/messages/{stub['id']}", params={"format": "metadata"}
            )
            out.append(self.summarise(full))
        logger.info("Search %r returned %d messages", query, len(out))
        return out

    def get_message(self, message_id: str) -> dict:
        """One message, with its body text."""
        full = self._request("GET", f"/messages/{message_id}", params={"format": "full"})
        return self.summarise(full, include_body=True)

    def get_thread(self, thread_id: str) -> dict:
        """A whole conversation, oldest message first."""
        thread = self._request("GET", f"/threads/{thread_id}", params={"format": "full"})
        return {
            "thread_id": thread.get("id"),
            "messages": [
                self.summarise(m, include_body=True) for m in thread.get("messages", [])
            ],
        }

    def list_attachments(self, message_id: str) -> list[dict]:
        """What is attached to a message, without downloading anything."""
        full = self._request("GET", f"/messages/{message_id}", params={"format": "full"})

        found: list[dict] = []

        def walk(part: dict) -> None:
            body = part.get("body") or {}
            if body.get("attachmentId"):
                found.append(
                    {
                        "attachment_id": body["attachmentId"],
                        "filename": part.get("filename"),
                        "mime_type": part.get("mimeType"),
                        "size_bytes": body.get("size"),
                    }
                )
            for child in part.get("parts") or []:
                walk(child)

        walk(full.get("payload") or {})
        return found

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------

    def list_labels(self) -> list[dict]:
        return self._request("GET", "/labels").get("labels", [])

    def create_label(self, name: str) -> dict:
        return self._request("POST", "/labels", body={"name": name})

    def delete_label(self, label_id: str) -> dict:
        self._request("DELETE", f"/labels/{label_id}")
        return {"deleted": label_id}

    def modify_labels(
        self,
        message_id: str,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> dict:
        return self._request(
            "POST",
            f"/messages/{message_id}/modify",
            body={"addLabelIds": add or [], "removeLabelIds": remove or []},
        )

    def trash(self, message_id: str) -> dict:
        return self._request("POST", f"/messages/{message_id}/trash")

    def untrash(self, message_id: str) -> dict:
        return self._request("POST", f"/messages/{message_id}/untrash")

    # ------------------------------------------------------------------
    # Composing
    # ------------------------------------------------------------------

    def _raw(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str | None = None,
        bcc: str | None = None,
        thread_headers: dict | None = None,
    ) -> str:
        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        if cc:
            message["Cc"] = cc
        if bcc:
            message["Bcc"] = bcc
        for key, value in (thread_headers or {}).items():
            message[key] = value
        message.set_content(body)
        return base64.urlsafe_b64encode(message.as_bytes()).decode()

    def list_drafts(self, limit: int = 20) -> list[dict]:
        listing = self._request("GET", "/drafts", params={"maxResults": min(limit, 100)})
        out = []
        for stub in listing.get("drafts", []) or []:
            draft = self._request("GET", f"/drafts/{stub['id']}", params={"format": "metadata"})
            out.append(
                {"draft_id": draft.get("id"), **self.summarise(draft.get("message") or {})}
            )
        return out

    def get_draft(self, draft_id: str) -> dict:
        draft = self._request("GET", f"/drafts/{draft_id}", params={"format": "full"})
        return {
            "draft_id": draft.get("id"),
            **self.summarise(draft.get("message") or {}, include_body=True),
        }

    def create_draft(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str | None = None,
        bcc: str | None = None,
        reply_to_message_id: str | None = None,
    ) -> dict:
        message: dict[str, Any] = {"raw": None}
        headers: dict[str, str] = {}

        if reply_to_message_id:
            original = self._request(
                "GET", f"/messages/{reply_to_message_id}", params={"format": "metadata"}
            )
            message_id_header = self._header(original, "Message-Id")
            if message_id_header:
                headers["In-Reply-To"] = message_id_header
                headers["References"] = message_id_header
            message["threadId"] = original.get("threadId")

        message["raw"] = self._raw(to, subject, body, cc, bcc, headers)
        draft = self._request("POST", "/drafts", body={"message": message})
        logger.info("Created draft %s to %s", draft.get("id"), to)
        return {"draft_id": draft.get("id"), "thread_id": (draft.get("message") or {}).get("threadId")}

    def update_draft(
        self,
        draft_id: str,
        to: str,
        subject: str,
        body: str,
        cc: str | None = None,
        bcc: str | None = None,
    ) -> dict:
        draft = self._request(
            "PUT",
            f"/drafts/{draft_id}",
            body={"message": {"raw": self._raw(to, subject, body, cc, bcc)}},
        )
        return {"draft_id": draft.get("id")}

    def delete_draft(self, draft_id: str) -> dict:
        self._request("DELETE", f"/drafts/{draft_id}")
        return {"deleted": draft_id}

    def send_draft(self, draft_id: str) -> dict:
        sent = self._request("POST", "/drafts/send", body={"id": draft_id})
        logger.warning("Sent draft %s as message %s", draft_id, sent.get("id"))
        return {"message_id": sent.get("id"), "thread_id": sent.get("threadId")}

    def send_message(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str | None = None,
        bcc: str | None = None,
    ) -> dict:
        sent = self._request(
            "POST", "/messages/send", body={"raw": self._raw(to, subject, body, cc, bcc)}
        )
        logger.warning("Sent message %s to %s", sent.get("id"), to)
        return {"message_id": sent.get("id"), "thread_id": sent.get("threadId")}
