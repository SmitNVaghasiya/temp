from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from services.database import get_db_client
from services.auth import hash_password, create_access_token, verify_password
from datetime import datetime, timedelta
import os
import logging
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
import random

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create APIRouter with prefix and tags
router = APIRouter(prefix="/auth", tags=["auth"])

# Twilio configuration
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN else None

# Pydantic models
class UserRegister(BaseModel):
    username: str
    mobileNo: str
    password: str
    otp: str

class UserOut(BaseModel):
    id: str
    username: str
    mobileNo: str
    created_at: str
    access_token: str

class OtpRequest(BaseModel):
    mobileNo: str

class OtpVerify(BaseModel):
    mobileNo: str
    otp: str

# --- Endpoints ---

@router.get("/check-user/{mobile_no}")
async def check_user(mobile_no: str):
    """Check if a user exists by mobile number using MongoDB."""
    try:
        client = get_db_client()
        db = client["jewelify"]
        user = db["users"].find_one({"mobileNo": mobile_no})
        return {"exists": bool(user)}
    except Exception as e:
        logger.error(f"Error checking user: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.post("/send-otp")
async def send_otp(request: OtpRequest):
    """Send OTP using Twilio."""
    if not twilio_client:
        logger.error("Twilio client not initialized. Check environment variables.")
        raise HTTPException(status_code=500, detail="Twilio configuration error")

    otp = str(random.randint(100000, 999999))  # Generate a 6-digit OTP
    try:
        message = twilio_client.messages.create(
            body=f"Your Jewelify OTP is {otp}",
            from_=TWILIO_PHONE_NUMBER,
            to=request.mobileNo
        )
        logger.info(f"OTP sent to {request.mobileNo}: {message.sid}")
    except TwilioRestException as e:
        logger.error(f"Twilio error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to send OTP: {str(e)}")

    # Store OTP in MongoDB temporarily
    try:
        client = get_db_client()
        db = client["jewelify"]
        db["otps"].delete_one({"mobileNo": request.mobileNo})  # Remove old OTP
        db["otps"].insert_one({
            "mobileNo": request.mobileNo,
            "otp": otp,
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": (datetime.utcnow() + timedelta(minutes=10)).isoformat()  # OTP expires in 10 minutes
        })
    except Exception as e:
        logger.error(f"Error storing OTP: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    return {"message": "OTP sent successfully"}

@router.post("/verify-otp")
async def verify_otp(request: OtpVerify):
    """Verify the OTP sent to the user's mobile number."""
    try:
        client = get_db_client()
        db = client["jewelify"]
        otp_record = db["otps"].find_one({"mobileNo": request.mobileNo})
        if not otp_record:
            raise HTTPException(status_code=400, detail="OTP not found or expired")

        # Check if OTP has expired
        expires_at = datetime.fromisoformat(otp_record["expires_at"])
        if datetime.utcnow() > expires_at:
            db["otps"].delete_one({"mobileNo": request.mobileNo})
            raise HTTPException(status_code=400, detail="OTP has expired")

        if otp_record["otp"] != request.otp:
            raise HTTPException(status_code=400, detail="Invalid OTP")

        # OTP is valid, delete it from the database
        db["otps"].delete_one({"mobileNo": request.mobileNo})
        return {"message": "OTP verified successfully"}
    except Exception as e:
        logger.error(f"Error verifying OTP: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.post("/register", response_model=UserOut)
async def register(user: UserRegister):
    """Register a new user after OTP verification, using MongoDB."""
    try:
        client = get_db_client()
        db = client["jewelify"]
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        raise HTTPException(status_code=500, detail=f"Database connection error: {str(e)}")

    # Check if username or mobile number already exists
    if db["users"].find_one({"username": user.username}):
        raise HTTPException(status_code=400, detail="Username already exists")
    if db["users"].find_one({"mobileNo": user.mobileNo}):
        raise HTTPException(status_code=400, detail="Mobile number already exists")

    # Hash password
    hashed_password = hash_password(user.password)
    
    # Save user data
    user_data = {
        "username": user.username,
        "mobileNo": user.mobileNo,
        "hashed_password": hashed_password,
        "created_at": datetime.utcnow().isoformat(),
    }
    
    try:
        result = db["users"].insert_one(user_data)
        access_token = create_access_token(data={"sub": str(result.inserted_id)})
    except Exception as e:
        logger.error(f"Error registering user: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create user: {str(e)}")
    
    return {
        "id": str(result.inserted_id),
        "username": user.username,
        "mobileNo": user.mobileNo,
        "created_at": user_data["created_at"],
        "access_token": access_token
    }

@router.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """Login a user using username/mobileNo and password, using MongoDB."""
    try:
        client = get_db_client()
        db = client["jewelify"]
        user = db["users"].find_one({
            "$or": [
                {"username": form_data.username},
                {"mobileNo": form_data.username}
            ]
        })
    except Exception as e:
        logger.error(f"Database error during login: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    if not user or not verify_password(form_data.password, user["hashed_password"]):
        raise HTTPException(status_code=400, detail="Incorrect username/mobileNo or password")

    access_token = create_access_token(data={"sub": str(user["_id"])})
    return {"access_token": access_token, "token_type": "bearer"}