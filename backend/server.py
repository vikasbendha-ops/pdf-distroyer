from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, UploadFile, File, Response, Header
from fastapi.responses import FileResponse, StreamingResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict, EmailStr
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone, timedelta
import secrets
import hashlib
import json
import aiofiles
import aiofiles.os
from jose import JWTError, jwt
from passlib.context import CryptContext
import httpx

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# JWT Configuration
SECRET_KEY = os.environ.get('JWT_SECRET_KEY', secrets.token_urlsafe(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# PDF Storage
PDF_STORAGE_PATH = Path(os.environ.get('PDF_STORAGE_PATH', '/app/backend/storage/pdfs'))
PDF_STORAGE_PATH.mkdir(parents=True, exist_ok=True)

# Stripe Configuration
STRIPE_API_KEY = os.environ.get('STRIPE_API_KEY', 'sk_test_emergent')

# Subscription Plans
SUBSCRIPTION_PLANS = {
    "basic": {"price": 5.00, "name": "Basic", "storage_mb": 500, "links_per_month": 50},
    "pro": {"price": 15.00, "name": "Pro", "storage_mb": 2000, "links_per_month": 200},
    "enterprise": {"price": 49.00, "name": "Enterprise", "storage_mb": 10000, "links_per_month": 1000}
}

# Create the main app
app = FastAPI(title="Autodestroy PDF Platform")

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==================== MODELS ====================

class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str
    language: Optional[str] = "en"

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    user_id: str
    name: str
    email: str
    role: str
    subscription_status: str
    plan: str
    storage_used: int
    language: Optional[str] = "en"
    created_at: datetime

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse

class PDFUploadResponse(BaseModel):
    pdf_id: str
    filename: str
    file_size: int
    folder: Optional[str] = None
    created_at: datetime

class PDFResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    pdf_id: str
    user_id: str
    filename: str
    original_filename: Optional[str] = None
    file_size: int
    folder: Optional[str] = None
    created_at: datetime

class PDFRename(BaseModel):
    filename: str

class PDFMove(BaseModel):
    folder: Optional[str] = None

class FolderCreate(BaseModel):
    name: str

class LinkCreate(BaseModel):
    pdf_id: str
    expiry_mode: str  # "countdown", "fixed", "manual"
    expiry_hours: Optional[int] = 0
    expiry_minutes: Optional[int] = 0
    expiry_seconds: Optional[int] = 0
    expiry_fixed_datetime: Optional[datetime] = None
    custom_expired_url: Optional[str] = None
    custom_expired_message: Optional[str] = None

class LinkResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    link_id: str
    pdf_id: str
    user_id: str
    token: str
    expiry_mode: str
    expiry_duration_seconds: Optional[int] = None
    expiry_fixed_datetime: Optional[datetime] = None
    first_open_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    open_count: int
    unique_ips: List[str]
    status: str
    custom_expired_url: Optional[str] = None
    custom_expired_message: Optional[str] = None
    created_at: datetime
    full_url: Optional[str] = None

class LinkAccessResponse(BaseModel):
    status: str
    pdf_url: Optional[str] = None
    expires_at: Optional[datetime] = None
    remaining_seconds: Optional[int] = None
    watermark_data: Optional[Dict[str, Any]] = None
    custom_expired_url: Optional[str] = None
    custom_expired_message: Optional[str] = None
    viewer_id: Optional[str] = None

class SubscriptionCreate(BaseModel):
    plan: str
    origin_url: str

class StripeConfigUpdate(BaseModel):
    stripe_key: Optional[str] = None
    mode: Optional[str] = None  # "sandbox" or "live"

class DomainCreate(BaseModel):
    domain: str

class AdminUserUpdate(BaseModel):
    subscription_status: Optional[str] = None
    plan: Optional[str] = None
    role: Optional[str] = None

class PasswordReset(BaseModel):
    email: EmailStr

class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str

class LanguageUpdate(BaseModel):
    language: str

# ==================== AUTH HELPERS ====================

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(request: Request) -> dict:
    credentials_exception = HTTPException(status_code=401, detail="Could not validate credentials")
    
    # Check cookie first
    token = request.cookies.get("session_token")
    
    # Then check Authorization header
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
    
    if not token:
        raise credentials_exception
    
    # Check if it's a session token (from Google OAuth)
    session = await db.user_sessions.find_one({"session_token": token}, {"_id": 0})
    if session:
        expires_at = session.get("expires_at")
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="Session expired")
        
        user = await db.users.find_one({"user_id": session["user_id"]}, {"_id": 0})
        if not user:
            raise credentials_exception
        return user
    
    # Try JWT token
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    if user is None:
        raise credentials_exception
    
    return user

async def get_current_admin(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

async def get_optional_user(request: Request) -> Optional[dict]:
    try:
        return await get_current_user(request)
    except:
        return None

# ==================== AUTH ENDPOINTS ====================

@api_router.post("/auth/register", response_model=TokenResponse)
async def register(user_data: UserCreate):
    existing = await db.users.find_one({"email": user_data.email}, {"_id": 0})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    user_id = f"user_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    
    user_doc = {
        "user_id": user_id,
        "name": user_data.name,
        "email": user_data.email,
        "password_hash": get_password_hash(user_data.password),
        "role": "user",
        "subscription_status": "inactive",
        "plan": "none",
        "storage_used": 0,
        "language": user_data.language or "en",
        "created_at": now.isoformat()
    }
    
    await db.users.insert_one(user_doc)
    
    access_token = create_access_token(data={"sub": user_id})
    user_doc.pop("password_hash", None)
    user_doc["created_at"] = now
    
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        user=UserResponse(**user_doc)
    )

@api_router.post("/auth/login", response_model=TokenResponse)
async def login(user_data: UserLogin, response: Response):
    user = await db.users.find_one({"email": user_data.email}, {"_id": 0})
    if not user or not verify_password(user_data.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    access_token = create_access_token(data={"sub": user["user_id"]})
    
    # Set cookie
    response.set_cookie(
        key="session_token",
        value=access_token,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=60*60*24*7,
        path="/"
    )
    
    user.pop("password_hash", None)
    if isinstance(user.get("created_at"), str):
        user["created_at"] = datetime.fromisoformat(user["created_at"])
    
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        user=UserResponse(**user)
    )

@api_router.post("/auth/google/session")
async def google_auth_session(request: Request, response: Response):
    body = await request.json()
    session_id = body.get("session_id")
    
    if not session_id:
        raise HTTPException(status_code=400, detail="Session ID required")
    
    # Exchange session_id with Emergent Auth
    async with httpx.AsyncClient() as http_client:
        resp = await http_client.get(
            "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
            headers={"X-Session-ID": session_id}
        )
        
        if resp.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid session")
        
        google_data = resp.json()
    
    email = google_data.get("email")
    name = google_data.get("name")
    picture = google_data.get("picture")
    session_token = google_data.get("session_token")
    
    # Find or create user
    existing_user = await db.users.find_one({"email": email}, {"_id": 0})
    
    if existing_user:
        user_id = existing_user["user_id"]
        # Update user info
        await db.users.update_one(
            {"user_id": user_id},
            {"$set": {"name": name, "picture": picture}}
        )
    else:
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        user_doc = {
            "user_id": user_id,
            "name": name,
            "email": email,
            "picture": picture,
            "role": "user",
            "subscription_status": "inactive",
            "plan": "none",
            "storage_used": 0,
            "language": "en",
            "created_at": now.isoformat()
        }
        await db.users.insert_one(user_doc)
    
    # Store session
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    await db.user_sessions.insert_one({
        "user_id": user_id,
        "session_token": session_token,
        "expires_at": expires_at.isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat()
    })
    
    # Set cookie
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=60*60*24*7,
        path="/"
    )
    
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0})
    user.pop("password_hash", None)
    if isinstance(user.get("created_at"), str):
        user["created_at"] = datetime.fromisoformat(user["created_at"])
    
    return {"user": UserResponse(**user)}

@api_router.get("/auth/me", response_model=UserResponse)
async def get_me(request: Request):
    user = await get_current_user(request)
    user.pop("password_hash", None)
    if isinstance(user.get("created_at"), str):
        user["created_at"] = datetime.fromisoformat(user["created_at"])
    return UserResponse(**user)

@api_router.post("/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get("session_token")
    if token:
        await db.user_sessions.delete_one({"session_token": token})
    
    response.delete_cookie(key="session_token", path="/")
    return {"message": "Logged out successfully"}

@api_router.put("/auth/language")
async def update_language(data: LanguageUpdate, request: Request):
    user = await get_current_user(request)
    
    # Validate language code
    valid_languages = ['en', 'es', 'fr', 'de', 'pt', 'it', 'nl', 'ru', 'zh', 'ja', 'ko', 'ar', 'hi', 'tr', 'pl', 'sv', 'no', 'da', 'fi', 'el', 'cs', 'ro', 'hu', 'th', 'vi', 'id', 'ms', 'fil', 'uk', 'he', 'sl']
    if data.language not in valid_languages:
        raise HTTPException(status_code=400, detail="Invalid language code")
    
    await db.users.update_one(
        {"user_id": user["user_id"]},
        {"$set": {"language": data.language}}
    )
    
    return {"message": "Language updated successfully", "language": data.language}

@api_router.post("/auth/password-reset")
async def request_password_reset(data: PasswordReset):
    user = await db.users.find_one({"email": data.email}, {"_id": 0})
    if not user:
        # Don't reveal if email exists
        return {"message": "If email exists, reset link will be sent"}
    
    reset_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    
    await db.password_resets.insert_one({
        "user_id": user["user_id"],
        "token": reset_token,
        "expires_at": expires_at.isoformat(),
        "used": False
    })
    
    # In production, send email with reset link
    logger.info(f"Password reset token for {data.email}: {reset_token}")
    
    return {"message": "If email exists, reset link will be sent", "token": reset_token}

@api_router.post("/auth/password-reset/confirm")
async def confirm_password_reset(data: PasswordResetConfirm):
    reset = await db.password_resets.find_one({"token": data.token, "used": False}, {"_id": 0})
    if not reset:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    
    expires_at = datetime.fromisoformat(reset["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Reset token expired")
    
    new_hash = get_password_hash(data.new_password)
    await db.users.update_one(
        {"user_id": reset["user_id"]},
        {"$set": {"password_hash": new_hash}}
    )
    
    await db.password_resets.update_one(
        {"token": data.token},
        {"$set": {"used": True}}
    )
    
    return {"message": "Password reset successfully"}

# ==================== FOLDER ENDPOINTS ====================

@api_router.get("/folders")
async def get_folders(request: Request):
    user = await get_current_user(request)
    folders = await db.folders.find({"user_id": user["user_id"]}, {"_id": 0}).to_list(100)
    return folders

@api_router.post("/folders")
async def create_folder(folder_data: FolderCreate, request: Request):
    user = await get_current_user(request)
    
    folder_id = f"folder_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    
    folder_doc = {
        "folder_id": folder_id,
        "user_id": user["user_id"],
        "name": folder_data.name,
        "created_at": now.isoformat()
    }
    
    await db.folders.insert_one(folder_doc)
    folder_doc["created_at"] = now
    return folder_doc

@api_router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str, request: Request):
    user = await get_current_user(request)
    
    # Move PDFs in folder to root
    await db.pdfs.update_many(
        {"user_id": user["user_id"], "folder": folder_id},
        {"$set": {"folder": None}}
    )
    
    result = await db.folders.delete_one({"folder_id": folder_id, "user_id": user["user_id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Folder not found")
    
    return {"message": "Folder deleted successfully"}

# ==================== PDF ENDPOINTS ====================

@api_router.post("/pdfs/upload", response_model=PDFUploadResponse)
async def upload_pdf(request: Request, file: UploadFile = File(...)):
    user = await get_current_user(request)
    
    # Check subscription
    if user.get("subscription_status") != "active":
        raise HTTPException(status_code=403, detail="Active subscription required")
    
    # Validate file type
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    
    # Read file
    content = await file.read()
    file_size = len(content)
    
    # Check storage limit
    plan = user.get("plan", "basic")
    plan_info = SUBSCRIPTION_PLANS.get(plan, SUBSCRIPTION_PLANS["basic"])
    max_storage = plan_info["storage_mb"] * 1024 * 1024
    
    if user.get("storage_used", 0) + file_size > max_storage:
        raise HTTPException(status_code=400, detail="Storage limit exceeded")
    
    # Generate unique filename
    pdf_id = f"pdf_{uuid.uuid4().hex[:12]}"
    safe_filename = f"{pdf_id}_{secrets.token_hex(8)}.pdf"
    file_path = PDF_STORAGE_PATH / user["user_id"] / safe_filename
    
    # Create user directory
    file_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Save file
    async with aiofiles.open(file_path, 'wb') as f:
        await f.write(content)
    
    now = datetime.now(timezone.utc)
    
    # Save to database
    pdf_doc = {
        "pdf_id": pdf_id,
        "user_id": user["user_id"],
        "filename": file.filename,
        "original_filename": file.filename,
        "file_path": str(file_path),
        "file_size": file_size,
        "folder": None,
        "created_at": now.isoformat()
    }
    await db.pdfs.insert_one(pdf_doc)
    
    # Update user storage
    await db.users.update_one(
        {"user_id": user["user_id"]},
        {"$inc": {"storage_used": file_size}}
    )
    
    return PDFUploadResponse(
        pdf_id=pdf_id,
        filename=file.filename,
        file_size=file_size,
        folder=None,
        created_at=now
    )

@api_router.get("/pdfs", response_model=List[PDFResponse])
async def get_pdfs(request: Request, folder: Optional[str] = None):
    user = await get_current_user(request)
    
    query = {"user_id": user["user_id"]}
    if folder:
        query["folder"] = folder
    
    pdfs = await db.pdfs.find(query, {"_id": 0}).to_list(1000)
    
    for pdf in pdfs:
        if isinstance(pdf.get("created_at"), str):
            pdf["created_at"] = datetime.fromisoformat(pdf["created_at"])
        if "original_filename" not in pdf:
            pdf["original_filename"] = pdf["filename"]
    
    return [PDFResponse(**pdf) for pdf in pdfs]

@api_router.put("/pdfs/{pdf_id}/rename")
async def rename_pdf(pdf_id: str, data: PDFRename, request: Request):
    user = await get_current_user(request)
    
    result = await db.pdfs.update_one(
        {"pdf_id": pdf_id, "user_id": user["user_id"]},
        {"$set": {"filename": data.filename}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="PDF not found")
    
    return {"message": "PDF renamed successfully", "filename": data.filename}

@api_router.put("/pdfs/{pdf_id}/move")
async def move_pdf(pdf_id: str, data: PDFMove, request: Request):
    user = await get_current_user(request)
    
    # Verify folder exists if specified
    if data.folder:
        folder = await db.folders.find_one({"folder_id": data.folder, "user_id": user["user_id"]})
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
    
    result = await db.pdfs.update_one(
        {"pdf_id": pdf_id, "user_id": user["user_id"]},
        {"$set": {"folder": data.folder}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="PDF not found")
    
    return {"message": "PDF moved successfully"}

@api_router.delete("/pdfs/{pdf_id}")
async def delete_pdf(pdf_id: str, request: Request):
    user = await get_current_user(request)
    
    pdf = await db.pdfs.find_one({"pdf_id": pdf_id, "user_id": user["user_id"]}, {"_id": 0})
    if not pdf:
        raise HTTPException(status_code=404, detail="PDF not found")
    
    # Delete file
    file_path = Path(pdf["file_path"])
    if file_path.exists():
        await aiofiles.os.remove(file_path)
    
    # Delete from database
    await db.pdfs.delete_one({"pdf_id": pdf_id})
    
    # Update storage
    await db.users.update_one(
        {"user_id": user["user_id"]},
        {"$inc": {"storage_used": -pdf["file_size"]}}
    )
    
    # Revoke all links for this PDF
    await db.links.update_many(
        {"pdf_id": pdf_id},
        {"$set": {"status": "revoked"}}
    )
    
    return {"message": "PDF deleted successfully"}

# ==================== LINK ENDPOINTS ====================

@api_router.post("/links", response_model=LinkResponse)
async def create_link(link_data: LinkCreate, request: Request):
    user = await get_current_user(request)
    
    # Verify PDF ownership
    pdf = await db.pdfs.find_one({"pdf_id": link_data.pdf_id, "user_id": user["user_id"]}, {"_id": 0})
    if not pdf:
        raise HTTPException(status_code=404, detail="PDF not found")
    
    # Check subscription
    if user.get("subscription_status") != "active":
        raise HTTPException(status_code=403, detail="Active subscription required")
    
    link_id = f"link_{uuid.uuid4().hex[:12]}"
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    
    expiry_duration_seconds = None
    expires_at = None
    
    if link_data.expiry_mode == "countdown":
        expiry_duration_seconds = (
            link_data.expiry_hours * 3600 +
            link_data.expiry_minutes * 60 +
            link_data.expiry_seconds
        )
    elif link_data.expiry_mode == "fixed" and link_data.expiry_fixed_datetime:
        expires_at = link_data.expiry_fixed_datetime
    
    link_doc = {
        "link_id": link_id,
        "pdf_id": link_data.pdf_id,
        "user_id": user["user_id"],
        "token": token,
        "expiry_mode": link_data.expiry_mode,
        "expiry_duration_seconds": expiry_duration_seconds,
        "expiry_fixed_datetime": expires_at.isoformat() if expires_at else None,
        "first_open_at": None,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "open_count": 0,
        "unique_ips": [],
        "ip_sessions": {},  # Track per-IP countdown sessions
        "status": "active",
        "custom_expired_url": link_data.custom_expired_url,
        "custom_expired_message": link_data.custom_expired_message,
        "created_at": now.isoformat(),
        "access_log": []  # Detailed access log
    }
    
    await db.links.insert_one(link_doc)
    
    link_doc["created_at"] = now
    link_doc["expires_at"] = expires_at
    link_doc["expiry_fixed_datetime"] = expires_at
    
    return LinkResponse(**link_doc)

@api_router.get("/links", response_model=List[LinkResponse])
async def get_links(request: Request):
    user = await get_current_user(request)
    links = await db.links.find({"user_id": user["user_id"]}, {"_id": 0}).to_list(1000)
    
    for link in links:
        for field in ["created_at", "first_open_at", "expires_at", "expiry_fixed_datetime"]:
            if isinstance(link.get(field), str):
                link[field] = datetime.fromisoformat(link[field])
    
    return [LinkResponse(**link) for link in links]

@api_router.get("/links/{link_id}/stats")
async def get_link_stats(link_id: str, request: Request):
    user = await get_current_user(request)
    
    link = await db.links.find_one({"link_id": link_id, "user_id": user["user_id"]}, {"_id": 0})
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    
    return {
        "link_id": link_id,
        "open_count": link.get("open_count", 0),
        "unique_ips": link.get("unique_ips", []),
        "unique_ip_count": len(link.get("unique_ips", [])),
        "access_log": link.get("access_log", [])[-50:],  # Last 50 accesses
        "ip_sessions": link.get("ip_sessions", {}),
        "status": link.get("status"),
        "created_at": link.get("created_at"),
        "first_open_at": link.get("first_open_at"),
        "expires_at": link.get("expires_at")
    }

@api_router.delete("/links/{link_id}")
async def delete_link(link_id: str, request: Request):
    user = await get_current_user(request)
    
    result = await db.links.delete_one({"link_id": link_id, "user_id": user["user_id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Link not found")
    
    return {"message": "Link deleted successfully"}

@api_router.post("/links/{link_id}/revoke")
async def revoke_link(link_id: str, request: Request):
    user = await get_current_user(request)
    
    result = await db.links.update_one(
        {"link_id": link_id, "user_id": user["user_id"]},
        {"$set": {"status": "revoked"}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Link not found")
    
    return {"message": "Link revoked successfully"}

# ==================== PUBLIC VIEWER ENDPOINTS ====================

@api_router.get("/view/{token}")
async def access_link(token: str, request: Request):
    link = await db.links.find_one({"token": token}, {"_id": 0})
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    
    # Check link status
    if link["status"] == "revoked":
        return LinkAccessResponse(
            status="revoked",
            custom_expired_url=link.get("custom_expired_url"),
            custom_expired_message=link.get("custom_expired_message") or "This link has been revoked"
        )
    
    # Check user subscription
    user = await db.users.find_one({"user_id": link["user_id"]}, {"_id": 0})
    if not user or user.get("subscription_status") != "active":
        return LinkAccessResponse(
            status="expired",
            custom_expired_message="The owner's subscription is inactive"
        )
    
    now = datetime.now(timezone.utc)
    client_ip = request.client.host if request.client else "unknown"
    viewer_id = f"{client_ip}_{secrets.token_hex(4)}"  # Unique viewer session
    
    # Handle expiry logic
    if link["expiry_mode"] == "countdown":
        # For countdown mode, each IP gets their own countdown!
        ip_sessions = link.get("ip_sessions", {})
        
        if client_ip in ip_sessions:
            # This IP has already opened the link
            ip_session = ip_sessions[client_ip]
            first_open = datetime.fromisoformat(ip_session["first_open"])
            if first_open.tzinfo is None:
                first_open = first_open.replace(tzinfo=timezone.utc)
            expires_at = first_open + timedelta(seconds=link["expiry_duration_seconds"])
            
            if now >= expires_at:
                # This IP's session has expired
                return LinkAccessResponse(
                    status="expired",
                    custom_expired_url=link.get("custom_expired_url"),
                    custom_expired_message=link.get("custom_expired_message") or "Your viewing session has expired"
                )
            
            remaining_seconds = max(0, int((expires_at - now).total_seconds()))
        else:
            # New IP - start their countdown
            ip_sessions[client_ip] = {
                "first_open": now.isoformat(),
                "expires_at": (now + timedelta(seconds=link["expiry_duration_seconds"])).isoformat()
            }
            expires_at = now + timedelta(seconds=link["expiry_duration_seconds"])
            remaining_seconds = link["expiry_duration_seconds"]
            
            # Update first_open_at if this is the very first access
            if not link.get("first_open_at"):
                await db.links.update_one(
                    {"token": token},
                    {"$set": {"first_open_at": now.isoformat()}}
                )
            
            # Save IP session
            await db.links.update_one(
                {"token": token},
                {"$set": {f"ip_sessions.{client_ip}": ip_sessions[client_ip]}}
            )
    
    elif link["expiry_mode"] == "fixed" and link.get("expires_at"):
        expires_at_str = link["expires_at"]
        if isinstance(expires_at_str, str):
            expires_at = datetime.fromisoformat(expires_at_str)
        else:
            expires_at = expires_at_str
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        
        if now >= expires_at:
            await db.links.update_one({"token": token}, {"$set": {"status": "expired"}})
            return LinkAccessResponse(
                status="expired",
                custom_expired_url=link.get("custom_expired_url"),
                custom_expired_message=link.get("custom_expired_message") or "This link has expired"
            )
        
        remaining_seconds = max(0, int((expires_at - now).total_seconds()))
    else:
        # Manual mode - no expiry
        expires_at = None
        remaining_seconds = None
    
    # Log access
    access_entry = {
        "ip": client_ip,
        "timestamp": now.isoformat(),
        "user_agent": request.headers.get("user-agent", "unknown")[:200]
    }
    
    # Update access stats
    update_ops = {
        "$inc": {"open_count": 1},
        "$push": {"access_log": {"$each": [access_entry], "$slice": -100}}  # Keep last 100
    }
    if client_ip not in link.get("unique_ips", []):
        update_ops["$addToSet"] = {"unique_ips": client_ip}
    
    await db.links.update_one({"token": token}, update_ops)
    
    return LinkAccessResponse(
        status="active",
        pdf_url=f"/api/view/{token}/pdf",
        expires_at=expires_at if expires_at else None,
        remaining_seconds=remaining_seconds,
        watermark_data={
            "ip": client_ip,
            "timestamp": now.isoformat(),
            "link_id": link["link_id"]
        },
        viewer_id=viewer_id
    )

@api_router.get("/view/{token}/pdf")
async def get_pdf_file(token: str, request: Request):
    link = await db.links.find_one({"token": token}, {"_id": 0})
    if not link or link["status"] == "revoked":
        raise HTTPException(status_code=404, detail="Link not found or revoked")
    
    client_ip = request.client.host if request.client else "unknown"
    now = datetime.now(timezone.utc)
    
    # Check expiry based on mode
    if link["expiry_mode"] == "countdown":
        ip_sessions = link.get("ip_sessions", {})
        if client_ip in ip_sessions:
            ip_session = ip_sessions[client_ip]
            expires_at = datetime.fromisoformat(ip_session["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if now >= expires_at:
                raise HTTPException(status_code=410, detail="Your viewing session has expired")
        else:
            # IP hasn't accessed yet, they need to call /view/{token} first
            raise HTTPException(status_code=403, detail="Please access the link first")
    
    elif link["expiry_mode"] == "fixed" and link.get("expires_at"):
        expires_at_str = link["expires_at"]
        expires_at = datetime.fromisoformat(expires_at_str) if isinstance(expires_at_str, str) else expires_at_str
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if now >= expires_at:
            raise HTTPException(status_code=410, detail="Link expired")
    
    pdf = await db.pdfs.find_one({"pdf_id": link["pdf_id"]}, {"_id": 0})
    if not pdf:
        raise HTTPException(status_code=404, detail="PDF not found")
    
    file_path = Path(pdf["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="PDF file not found")
    
    return FileResponse(
        file_path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store, no-cache, must-revalidate"
        }
    )

# ==================== SUBSCRIPTION ENDPOINTS ====================

@api_router.post("/subscription/checkout")
async def create_subscription_checkout(data: SubscriptionCreate, request: Request):
    user = await get_current_user(request)
    
    if data.plan not in SUBSCRIPTION_PLANS:
        raise HTTPException(status_code=400, detail="Invalid plan")
    
    plan_info = SUBSCRIPTION_PLANS[data.plan]
    
    # Create Stripe checkout
    from emergentintegrations.payments.stripe.checkout import StripeCheckout, CheckoutSessionRequest
    
    host_url = data.origin_url.rstrip('/')
    webhook_url = f"{host_url}/api/webhook/stripe"
    
    stripe_checkout = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=webhook_url)
    
    checkout_request = CheckoutSessionRequest(
        amount=plan_info["price"],
        currency="eur",
        success_url=f"{host_url}/dashboard?payment=success&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{host_url}/pricing?payment=cancelled",
        metadata={
            "user_id": user["user_id"],
            "plan": data.plan
        }
    )
    
    session = await stripe_checkout.create_checkout_session(checkout_request)
    
    # Store payment transaction
    await db.payment_transactions.insert_one({
        "transaction_id": f"txn_{uuid.uuid4().hex[:12]}",
        "user_id": user["user_id"],
        "session_id": session.session_id,
        "amount": plan_info["price"],
        "currency": "eur",
        "plan": data.plan,
        "payment_status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat()
    })
    
    return {"url": session.url, "session_id": session.session_id}

@api_router.get("/subscription/status/{session_id}")
async def check_subscription_status(session_id: str, request: Request):
    user = await get_current_user(request)
    
    from emergentintegrations.payments.stripe.checkout import StripeCheckout
    
    stripe_checkout = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url="")
    status = await stripe_checkout.get_checkout_status(session_id)
    
    if status.payment_status == "paid":
        # Get transaction
        txn = await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0})
        
        if txn and txn.get("payment_status") != "completed":
            # Update transaction
            await db.payment_transactions.update_one(
                {"session_id": session_id},
                {"$set": {"payment_status": "completed"}}
            )
            
            # Activate subscription
            plan = txn.get("plan", "basic")
            await db.users.update_one(
                {"user_id": user["user_id"]},
                {
                    "$set": {
                        "subscription_status": "active",
                        "plan": plan,
                        "subscription_expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
                    }
                }
            )
    
    return {
        "status": status.status,
        "payment_status": status.payment_status,
        "amount": status.amount_total / 100
    }

@api_router.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    from emergentintegrations.payments.stripe.checkout import StripeCheckout
    
    body = await request.body()
    signature = request.headers.get("Stripe-Signature")
    
    stripe_checkout = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url="")
    
    try:
        event = await stripe_checkout.handle_webhook(body, signature)
        
        if event.event_type == "checkout.session.completed":
            metadata = event.metadata or {}
            user_id = metadata.get("user_id")
            plan = metadata.get("plan", "basic")
            
            if user_id:
                await db.users.update_one(
                    {"user_id": user_id},
                    {
                        "$set": {
                            "subscription_status": "active",
                            "plan": plan,
                            "subscription_expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
                        }
                    }
                )
                
                await db.payment_transactions.update_one(
                    {"session_id": event.session_id},
                    {"$set": {"payment_status": "completed"}}
                )
        
        return {"received": True}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"received": True}

@api_router.get("/subscription/plans")
async def get_plans():
    return SUBSCRIPTION_PLANS

# ==================== ADMIN ENDPOINTS ====================

@api_router.get("/admin/users")
async def admin_get_users(request: Request):
    await get_current_admin(request)
    
    users = await db.users.find({}, {"_id": 0, "password_hash": 0}).to_list(1000)
    
    # Get PDF and link counts for each user
    for user in users:
        pdf_count = await db.pdfs.count_documents({"user_id": user["user_id"]})
        link_count = await db.links.count_documents({"user_id": user["user_id"]})
        user["pdf_count"] = pdf_count
        user["link_count"] = link_count
        if isinstance(user.get("created_at"), str):
            user["created_at"] = datetime.fromisoformat(user["created_at"])
    
    return users

@api_router.put("/admin/users/{user_id}")
async def admin_update_user(user_id: str, update_data: AdminUserUpdate, request: Request):
    await get_current_admin(request)
    
    update_dict = {k: v for k, v in update_data.model_dump().items() if v is not None}
    
    if not update_dict:
        raise HTTPException(status_code=400, detail="No update data provided")
    
    result = await db.users.update_one(
        {"user_id": user_id},
        {"$set": update_dict}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {"message": "User updated successfully"}

@api_router.delete("/admin/users/{user_id}")
async def admin_delete_user(user_id: str, request: Request):
    admin = await get_current_admin(request)
    
    if user_id == admin["user_id"]:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    
    # Delete user's PDFs
    user_pdfs = await db.pdfs.find({"user_id": user_id}, {"_id": 0}).to_list(1000)
    for pdf in user_pdfs:
        file_path = Path(pdf["file_path"])
        if file_path.exists():
            await aiofiles.os.remove(file_path)
    
    await db.pdfs.delete_many({"user_id": user_id})
    await db.links.delete_many({"user_id": user_id})
    await db.user_sessions.delete_many({"user_id": user_id})
    await db.folders.delete_many({"user_id": user_id})
    await db.users.delete_one({"user_id": user_id})
    
    return {"message": "User deleted successfully"}

@api_router.get("/admin/links")
async def admin_get_links(request: Request):
    await get_current_admin(request)
    
    links = await db.links.find({}, {"_id": 0}).to_list(1000)
    
    # Enrich with user and PDF info
    for link in links:
        user = await db.users.find_one({"user_id": link["user_id"]}, {"_id": 0, "name": 1, "email": 1})
        pdf = await db.pdfs.find_one({"pdf_id": link["pdf_id"]}, {"_id": 0, "filename": 1})
        
        link["user_name"] = user.get("name") if user else "Unknown"
        link["user_email"] = user.get("email") if user else "Unknown"
        link["pdf_name"] = pdf.get("filename") if pdf else "Unknown"
        
        for field in ["created_at", "first_open_at", "expires_at", "expiry_fixed_datetime"]:
            if isinstance(link.get(field), str):
                link[field] = datetime.fromisoformat(link[field])
    
    return links

@api_router.post("/admin/links/{link_id}/revoke")
async def admin_revoke_link(link_id: str, request: Request):
    await get_current_admin(request)
    
    result = await db.links.update_one(
        {"link_id": link_id},
        {"$set": {"status": "revoked"}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Link not found")
    
    return {"message": "Link revoked successfully"}

@api_router.delete("/admin/links/{link_id}")
async def admin_delete_link(link_id: str, request: Request):
    await get_current_admin(request)
    
    result = await db.links.delete_one({"link_id": link_id})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Link not found")
    
    return {"message": "Link deleted successfully"}

@api_router.get("/admin/stats")
async def admin_get_stats(request: Request):
    await get_current_admin(request)
    
    total_users = await db.users.count_documents({})
    active_subscribers = await db.users.count_documents({"subscription_status": "active"})
    total_pdfs = await db.pdfs.count_documents({})
    total_links = await db.links.count_documents({})
    active_links = await db.links.count_documents({"status": "active"})
    
    # Storage stats
    pipeline = [
        {"$group": {"_id": None, "total_storage": {"$sum": "$storage_used"}}}
    ]
    storage_result = await db.users.aggregate(pipeline).to_list(1)
    total_storage = storage_result[0]["total_storage"] if storage_result else 0
    
    # Total views
    views_pipeline = [
        {"$group": {"_id": None, "total_views": {"$sum": "$open_count"}}}
    ]
    views_result = await db.links.aggregate(views_pipeline).to_list(1)
    total_views = views_result[0]["total_views"] if views_result else 0
    
    # Unique viewers (count of all unique IPs across all links)
    unique_ips_pipeline = [
        {"$unwind": "$unique_ips"},
        {"$group": {"_id": "$unique_ips"}},
        {"$count": "total"}
    ]
    unique_result = await db.links.aggregate(unique_ips_pipeline).to_list(1)
    total_unique_viewers = unique_result[0]["total"] if unique_result else 0
    
    return {
        "total_users": total_users,
        "active_subscribers": active_subscribers,
        "total_pdfs": total_pdfs,
        "total_links": total_links,
        "active_links": active_links,
        "total_storage_bytes": total_storage,
        "total_views": total_views,
        "total_unique_viewers": total_unique_viewers
    }

# ==================== ADMIN PLATFORM SETTINGS ====================

@api_router.get("/admin/settings/stripe")
async def admin_get_stripe_settings(request: Request):
    await get_current_admin(request)
    doc = await db.platform_settings.find_one({"key": "stripe"}, {"_id": 0})
    current_env_key = STRIPE_API_KEY
    if doc:
        stored_key = doc.get("stripe_key", "")
        mode = doc.get("mode", "sandbox")
        has_live_key = bool(stored_key and stored_key.startswith("sk_live_"))
        active_key = stored_key if stored_key else current_env_key
    else:
        mode = "sandbox" if current_env_key.startswith("sk_test_") else "live"
        has_live_key = current_env_key.startswith("sk_live_")
        active_key = current_env_key
    
    return {
        "mode": mode,
        "has_live_key": has_live_key,
        "active_key_type": "live" if active_key.startswith("sk_live_") else "sandbox",
        "key_preview": f"sk_...{active_key[-4:]}" if active_key else "Not configured",
        "sandbox_active": not active_key.startswith("sk_live_"),
    }

@api_router.put("/admin/settings/stripe")
async def admin_update_stripe_settings(request: Request, config: StripeConfigUpdate):
    await get_current_admin(request)
    update_data = {"key": "stripe", "updated_at": datetime.now(timezone.utc).isoformat()}
    if config.stripe_key is not None:
        if config.stripe_key and not (config.stripe_key.startswith("sk_test_") or config.stripe_key.startswith("sk_live_")):
            raise HTTPException(status_code=400, detail="Invalid Stripe key format")
        update_data["stripe_key"] = config.stripe_key
    if config.mode is not None:
        if config.mode not in ["sandbox", "live"]:
            raise HTTPException(status_code=400, detail="Mode must be 'sandbox' or 'live'")
        update_data["mode"] = config.mode
    
    await db.platform_settings.update_one(
        {"key": "stripe"},
        {"$set": update_data},
        upsert=True
    )
    return {"message": "Stripe settings updated successfully"}


@api_router.post("/domains")
async def add_domain(domain_data: DomainCreate, request: Request):
    user = await get_current_user(request)
    
    # Check subscription - enterprise only
    if user.get("plan") != "enterprise":
        raise HTTPException(status_code=403, detail="Custom domains require Enterprise plan")
    
    domain_id = f"dom_{uuid.uuid4().hex[:12]}"
    verification_token = secrets.token_urlsafe(32)
    
    domain_doc = {
        "domain_id": domain_id,
        "user_id": user["user_id"],
        "domain": domain_data.domain,
        "verification_token": verification_token,
        "verification_status": "pending",
        "ssl_status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    await db.domains.insert_one(domain_doc)
    
    return {
        "domain_id": domain_id,
        "verification_token": verification_token,
        "cname_target": "autodestroy.example.com",
        "instructions": f"Add a TXT record with value: autodestroy-verify={verification_token}"
    }

@api_router.get("/domains")
async def get_domains(request: Request):
    user = await get_current_user(request)
    domains = await db.domains.find({"user_id": user["user_id"]}, {"_id": 0}).to_list(100)
    return domains

@api_router.delete("/domains/{domain_id}")
async def delete_domain(domain_id: str, request: Request):
    user = await get_current_user(request)
    
    result = await db.domains.delete_one({"domain_id": domain_id, "user_id": user["user_id"]})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Domain not found")
    
    return {"message": "Domain deleted successfully"}

# ==================== DASHBOARD STATS ====================

@api_router.get("/dashboard/stats")
async def get_dashboard_stats(request: Request):
    user = await get_current_user(request)
    
    pdf_count = await db.pdfs.count_documents({"user_id": user["user_id"]})
    link_count = await db.links.count_documents({"user_id": user["user_id"]})
    active_links = await db.links.count_documents({"user_id": user["user_id"], "status": "active"})
    expired_links = await db.links.count_documents({"user_id": user["user_id"], "status": "expired"})
    revoked_links = await db.links.count_documents({"user_id": user["user_id"], "status": "revoked"})
    
    # Get total views
    pipeline = [
        {"$match": {"user_id": user["user_id"]}},
        {"$group": {"_id": None, "total_views": {"$sum": "$open_count"}}}
    ]
    views_result = await db.links.aggregate(pipeline).to_list(1)
    total_views = views_result[0]["total_views"] if views_result else 0
    
    # Get unique viewers
    unique_pipeline = [
        {"$match": {"user_id": user["user_id"]}},
        {"$unwind": "$unique_ips"},
        {"$group": {"_id": "$unique_ips"}},
        {"$count": "total"}
    ]
    unique_result = await db.links.aggregate(unique_pipeline).to_list(1)
    unique_viewers = unique_result[0]["total"] if unique_result else 0
    
    # Get recent activity (last 7 days views)
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    activity_pipeline = [
        {"$match": {"user_id": user["user_id"]}},
        {"$unwind": "$access_log"},
        {"$match": {"access_log.timestamp": {"$gte": seven_days_ago}}},
        {"$count": "recent_views"}
    ]
    activity_result = await db.links.aggregate(activity_pipeline).to_list(1)
    recent_views = activity_result[0]["recent_views"] if activity_result else 0
    
    plan = user.get("plan", "none")
    plan_info = SUBSCRIPTION_PLANS.get(plan, {"storage_mb": 0, "links_per_month": 0})
    
    return {
        "pdf_count": pdf_count,
        "link_count": link_count,
        "active_links": active_links,
        "expired_links": expired_links,
        "revoked_links": revoked_links,
        "total_views": total_views,
        "unique_viewers": unique_viewers,
        "recent_views_7d": recent_views,
        "storage_used": user.get("storage_used", 0),
        "storage_limit": plan_info["storage_mb"] * 1024 * 1024,
        "plan": plan,
        "subscription_status": user.get("subscription_status", "inactive")
    }

# ==================== ROOT ENDPOINT ====================

@api_router.get("/")
async def root():
    return {"message": "Autodestroy PDF Platform API", "version": "1.0.0"}

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
