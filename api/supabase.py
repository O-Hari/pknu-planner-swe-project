import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", encoding="utf-8-sig")
    async with httpx.AsyncClient(timeout=15) as client:
        app.state.supabase_http = client
        yield


class Supabase:
    def __init__(self, http: httpx.AsyncClient, key: str, token: str | None = None):
        self.http = http
        self.url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        self.key = key
        self.token = token

    async def request(self, method: str, path: str, *, params=None, data=None, prefer=None):
        if not self.url or not self.key or self.key.startswith("your-"):
            raise HTTPException(503, "Supabase configuration is missing")
        headers = {"apikey": self.key}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        elif not self.key.startswith("sb_"):
            headers["Authorization"] = f"Bearer {self.key}"
        if prefer:
            headers["Prefer"] = prefer
        try:
            response = await self.http.request(
                method, f"{self.url}{path}", params=params, json=data, headers=headers
            )
        except httpx.TimeoutException:
            raise HTTPException(504, "Supabase request timed out") from None
        except (httpx.InvalidURL, httpx.UnsupportedProtocol):
            raise HTTPException(503, "Invalid Supabase URL") from None
        except httpx.RequestError:
            raise HTTPException(502, "Supabase connection failed") from None

        if response.is_success:
            if not response.content:
                return None
            try:
                return response.json()
            except ValueError:
                raise HTTPException(502, "Invalid Supabase response") from None

        try:
            error = response.json()
        except ValueError:
            error = {}
        if not isinstance(error, dict):
            error = {}
        code = error.get("code")
        message = error.get("message")
        auth_rejected = path == "/auth/v1/user" and response.status_code == 403
        if self.token and (response.status_code == 401 or auth_rejected):
            raise HTTPException(401, "Invalid Supabase access token", headers={"WWW-Authenticate": "Bearer"})
        if self.token and response.status_code == 403:
            raise HTTPException(403, "Access denied")
        if code in {"PT404", "PT409"} and message in {
            "timetable_not_found", "section_not_found", "different_term", "class_time_conflict",
        }:
            detail = {"code": message}
            if message == "class_time_conflict":
                detail["conflicting_section_ids"] = json.loads(error["details"])
            raise HTTPException(int(code[2:]), detail)
        if code == "23503":
            raise HTTPException(409, "Referenced resource no longer exists")
        if code == "23514":
            raise HTTPException(422, "Request violates a data constraint")
        if path.startswith("/storage/v1/object/sign/") and (
            response.status_code == 404 or str(error.get("statusCode")) == "404"
        ):
            raise HTTPException(404, {"code": "syllabus_file_not_found"})
        if response.status_code == 429:
            retry = response.headers.get("Retry-After")
            raise HTTPException(429, "Supabase rate limit reached", headers={"Retry-After": retry} if retry else None)
        LOGGER.warning(
            "Supabase request failed (%s %s, status=%s, code=%s)",
            method, path.split("/")[1], response.status_code, code,
        )
        raise HTTPException(502, "Supabase request failed")

    async def rest(self, table: str, method: str = "GET", *, params=None, data=None, prefer=None):
        return await self.request(method, f"/rest/v1/{table}", params=params, data=data, prefer=prefer)


def public_supabase(request: Request) -> Supabase:
    key = (os.environ.get("SUPABASE_PUBLISHABLE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")).strip()
    return Supabase(request.app.state.supabase_http, key)


def storage_supabase(request: Request) -> Supabase:
    return Supabase(request.app.state.supabase_http, os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip())


PublicDB = Annotated[Supabase, Depends(public_supabase)]
StorageDB = Annotated[Supabase, Depends(storage_supabase)]
Bearer = Annotated[HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))]


async def authenticated_supabase(public: PublicDB, credentials: Bearer) -> tuple[Supabase, str]:
    if not credentials:
        raise HTTPException(401, "Supabase access token required", headers={"WWW-Authenticate": "Bearer"})
    db = Supabase(public.http, public.key, credentials.credentials)
    user = await db.request("GET", "/auth/v1/user")
    try:
        return db, str(UUID(user["id"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise HTTPException(401, "Invalid Supabase user", headers={"WWW-Authenticate": "Bearer"}) from None


UserDB = Annotated[tuple[Supabase, str], Depends(authenticated_supabase)]
