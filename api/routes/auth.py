from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from pydantic import BaseModel
from services.database import get_db_client
from services.auth import hash_password, create_access_token, verify_password
from datetime import datetime, timedelta
import os
import logging
import requests  # For Fast2SMS API requests
import random
from jose import JWTError, jwt
from bson import ObjectId
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create APIRouter with prefix and tags
router = APIRouter(prefix="/auth", tags=["auth"])

# Fast2SMS configuration
FAST2SMS_API_KEY = os.getenv("FAST2SMS_API_KEY")
FAST2SMS_SENDER_ID = "FSTSMS"  # Default sender ID for free accounts; check your Fast2SMS dashboard
FAST2SMS_ROUTE = "otp"  # Use the OTP route for sending OTP messages

# JWT configuration for /auth/me
SECRET_KEY = os.getenv("SECRET_KEY", "default-secret-key")
ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

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

# Function to get the current user from the token (for /auth/me)
async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    # Fetch user from the database
    try:
        client = get_db_client()
        db = client["jewelify"]
        user = db["users"].find_one({"_id": ObjectId(user_id)})
        if user is None:
            raise credentials_exception
    except Exception as e:
        logger.error(f"Database error while fetching user: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    return user

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
    """Send OTP using Fast2SMS."""
    if not FAST2SMS_API_KEY:
        logger.error("Fast2SMS API key not found. Check environment variables.")
        raise HTTPException(status_code=500, detail="Fast2SMS configuration error")

    # Generate a 6-digit OTP
    otp = str(random.randint(100000, 999999))

    # Prepare the phone number for Fast2SMS (remove the '+' prefix for Indian numbers)
    mobile_no = request.mobileNo
    if mobile_no.startswith("+"):
        mobile_no = mobile_no[1:]  # Remove the '+' (e.g., +919723399XXXX -> 919723399XXXX)

    # Prepare the Fast2SMS API request
    fast2sms_url = "https://www.fast2sms.com/dev/bulkV2"
    params = {
        "authorization": FAST2SMS_API_KEY,
        "sender_id": FAST2SMS_SENDER_ID,
        "message": f"Your Jewelify OTP is {otp}",
        "route": FAST2SMS_ROUTE,
        "numbers": mobile_no
    }

    try:
        # Send the OTP using Fast2SMS
        response = requests.get(fast2sms_url, params=params)
        response_data = response.json()

        # Check if the message was sent successfully
        if response_data.get("return") is not True:
            error_message = response_data.get("message", "Unknown error")
            logger.error(f"Fast2SMS error: {error_message}")
            raise HTTPException(status_code=500, detail=f"Failed to send OTP: {error_message}")

        logger.info(f"OTP sent to {request.mobileNo} via Fast2SMS")
    except Exception as e:
        logger.error(f"Error sending OTP via Fast2SMS: {e}")
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

@router.get("/me")
async def get_user_details(current_user: dict = Depends(get_current_user)):
    """Fetch the details of the currently authenticated user."""
    try:
        return {
            "id": str(current_user["_id"]),
            "username": current_user["username"],
            "mobileNo": current_user["mobileNo"],
            "created_at": current_user["created_at"]
        }
    except Exception as e:
        logger.error(f"Error fetching user details: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch user details: {str(e)}")