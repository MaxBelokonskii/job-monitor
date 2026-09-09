from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Optional
from job_monitor.telegram_client import get_client

router = APIRouter(prefix="/api/auth", tags=["auth"])

_auth_state: dict = {}

class AuthRequest(BaseModel):
    phone: str

class AuthCode(BaseModel):
    phone: str
    code: str
    phone_hash: str
    password: Optional[str] = None

class MessageRequest(BaseModel):
    text: str

@router.get("/status")
async def auth_status() -> dict[str, Any]:
    client = get_client()
    try:
        await client.connect()
        authorized = await client.is_user_authorized()
        return {"authorized": authorized}
    except Exception as e:
        return {"authorized": False, "error": str(e)}
    finally:
        pass  # singleton — не отключаем

@router.post("/send-code")
async def send_code(body: AuthRequest) -> dict[str, str]:
    client = get_client()
    try:
        await client.connect()
        if await client.is_user_authorized():
            return {"status": "already_authorized"}
        result = await client.send_code_request(body.phone)
        _auth_state["phone"] = body.phone
        _auth_state["phone_hash"] = result.phone_code_hash
        return {"status": "code_sent", "phone_hash": result.phone_code_hash}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/verify-code")
async def verify_code(body: AuthCode) -> dict[str, str]:
    from telethon.errors import SessionPasswordNeededError
    client = get_client()
    try:
        await client.connect()
        try:
            await client.sign_in(body.phone, body.code, phone_code_hash=body.phone_hash)
        except SessionPasswordNeededError:
            if not body.password:
                raise HTTPException(status_code=428, detail="2FA_REQUIRED")
            await client.sign_in(password=body.password)
        return {"status": "authorized"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/messages/{username}")
async def get_messages(username: str, limit: int = 30) -> list[dict[str, Any]]:
    client = get_client()
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise HTTPException(status_code=401, detail="Не авторизован")
        messages = []
        async for msg in client.iter_messages(username.lstrip("@"), limit=limit):
            if not msg.text:
                continue
            messages.append({
                "id": msg.id,
                "text": msg.text,
                "date": msg.date.strftime("%Y-%m-%d %H:%M"),
                "out": msg.out,
            })
        return list(reversed(messages))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/messages/{username}")
async def send_message(username: str, body: MessageRequest) -> dict[str, str]:
    client = get_client()
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise HTTPException(status_code=401, detail="Не авторизован")
        await client.send_message(username.lstrip("@"), body.text)
        return {"status": "sent"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
