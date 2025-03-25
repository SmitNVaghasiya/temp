import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from pydantic import BaseModel, validator, EmailStr
from services.database import get_db_client
from services.auth import hash_password, create_access_token, verify_password
from datetime import datetime, timedelta
import logging
from jose import JWTError, jwt
from bson import ObjectId
from dotenv import load_dotenv
import random
import string
from emails import Message  # We only need Message

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create APIRouter with prefix and tags
router = APIRouter(prefix="/auth", tags=["auth"])

# JWT configuration for /auth/me
SECRET_KEY = os.getenv("SECRET_KEY", "default-secret-key")
ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# SMTP configuration for sending emails
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USERNAME = os.getenv("SMTP_USER")  # Changed to match .env variable name
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

# Constants
MAX_EMAIL_ATTEMPTS = 3  # Maximum number of email verification code resend attempts
EMAIL_COOLDOWN_MINUTES = 5  # Cooldown period after max attempts (in minutes)

# Pydantic models
class UserRegister(BaseModel):
    username: str
    email: EmailStr  # Use EmailStr for email validation
    password: str
    verification_code: str  # Renamed from 'otp' to 'verification_code' for clarity
    mobileNo: str | None = None  # Keep mobileNo for future use, but make it optional

    @validator("username")
    def validate_username(cls, v):
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters long")
        if not v.isalnum():
            raise ValueError("Username must contain only alphanumeric characters")
        return v

    @validator("password")
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        return v

class UserOut(BaseModel):
    id: str
    username: str
    email: str
    mobileNo: str | None  # Keep mobileNo for future use
    created_at: str
    access_token: str

class EmailVerificationRequest(BaseModel):
    email: EmailStr

class EmailVerificationVerify(BaseModel):
    email: EmailStr
    code: str

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

# Function to generate a random verification code
def generate_verification_code(length=6):
    return ''.join(random.choices(string.digits, k=length))

# Function to send verification code via email
async def send_verification_email(email: str, code: str):
    if not all([SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD]):
        logger.error("SMTP credentials not found. Check environment variables.")
        raise HTTPException(status_code=500, detail="SMTP configuration error")

    # Create the email message with an inline HTML template
    html_template = """
    <h1>Jewelify Email Verification</h1>
    <p>Your verification code is: <strong>{{ code }}</strong></p>
    <p>This code will expire in 10 minutes.</p>
    <p>If you did not request this code, please ignore this email.</p>
    """
    message = Message(
        subject="Jewelify Email Verification Code",
        mail_from=("Jewelify", SMTP_USERNAME),
        mail_to=email,
        html=html_template,  # Pass the HTML template directly
        context={"code": code}  # Pass the context for Jinja2 rendering
    )

    # Send the email
    try:
        response = message.send(
            smtp={
                "host": SMTP_HOST,
                "port": SMTP_PORT,
                "tls": True,
                "user": SMTP_USERNAME,
                "password": SMTP_PASSWORD
            }
        )
        if response.status_code not in [250]:
            logger.error(f"Failed to send email: {response.status_text}")
            raise HTTPException(status_code=500, detail=f"Failed to send verification email: {response.status_text}")
        logger.info(f"Verification email sent to {email} with code {code}")
    except Exception as e:
        logger.error(f"Error sending email: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error sending email: {str(e)}")

# --- Endpoints ---

@router.get("/check-user/{email}", description="Check if a user exists by email.")
async def check_user(email: str):
    """Check if a user exists by email using MongoDB."""
    try:
        client = get_db_client()
        db = client["jewelify"]
        user = db["users"].find_one({"email": email})
        return {"exists": bool(user)}
    except Exception as e:
        logger.error(f"Error checking user: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.post("/send-verification-email", description="Send a verification code to the user's email.")
async def send_verification_email_endpoint(request: EmailVerificationRequest):
    """Send a verification code to the user's email."""
    email = request.email

    # Check for existing verification session and retry attempts
    client = get_db_client()
    db = client["jewelify"]
    verification_record = db["verifications"].find_one({"email": email})
    if verification_record:
        attempts = verification_record.get("attempts", 0)  # Ensure attempts field exists
        if attempts >= MAX_EMAIL_ATTEMPTS:
            cooldown_expires = datetime.fromisoformat(verification_record["created_at"]) + timedelta(minutes=EMAIL_COOLDOWN_MINUTES)
            if datetime.utcnow() < cooldown_expires:
                remaining_seconds = (cooldown_expires - datetime.utcnow()).total_seconds()
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many email verification attempts. Please wait {int(remaining_seconds)} seconds before trying again."
                )
            else:
                # Reset attempts after cooldown
                attempts = 0

        # Increment attempts
        db["verifications"].update_one(
            {"email": email},
            {"$set": {"attempts": attempts + 1}}
        )

    # Generate a new verification code
    code = generate_verification_code()

    # Send the verification code via email
    await send_verification_email(email, code)

    # Store the verification code in MongoDB
    try:
        db["verifications"].delete_one({"email": email})  # Remove old record
        db["verifications"].insert_one({
            "email": email,
            "code": code,
            "attempts": 0,
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": (datetime.utcnow() + timedelta(minutes=10)).isoformat()
        })
    except Exception as e:
        logger.error(f"Error storing verification code: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    return {"message": "Verification code sent to your email"}

@router.post("/verify-email-code", description="Verify the email using the code sent to the user's email.")
async def verify_email_code(request: EmailVerificationVerify):
    """Verify the email using the code sent to the user's email."""
    email = request.email

    # Check the current state
    client = get_db_client()
    db = client["jewelify"]
    verification_record = db["verifications"].find_one({"email": email})
    if not verification_record:
        raise HTTPException(status_code=400, detail="Verification session not found or expired")

    # Check if the session has expired
    expires_at = datetime.fromisoformat(verification_record["expires_at"])
    if datetime.utcnow() > expires_at:
        db["verifications"].delete_one({"email": email})
        raise HTTPException(status_code=400, detail="Verification session has expired")

    # Verify the code
    if verification_record["code"] != request.code:
        raise HTTPException(status_code=400, detail="Invalid verification code")

    # Code is valid, delete the session from the database
    db["verifications"].delete_one({"email": email})
    logger.info(f"Email {email} verified successfully")
    return {"message": "Email verified successfully"}

@router.post("/register", response_model=UserOut, description="Register a new user after email verification.")
async def register(user: UserRegister):
    """Register a new user after email verification, using MongoDB."""
    try:
        client = get_db_client()
        db = client["jewelify"]
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        raise HTTPException(status_code=500, detail=f"Database connection error: {str(e)}")

    # Check if username or email already exists
    if db["users"].find_one({"username": user.username}):
        raise HTTPException(status_code=400, detail="Username already exists")
    if db["users"].find_one({"email": user.email}):
        raise HTTPException(status_code=400, detail="Email already exists")

    # Verify the email verification code
    verification_record = db["verifications"].find_one({"email": user.email})
    if not verification_record or verification_record["code"] != user.verification_code:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    # Hash password
    hashed_password = hash_password(user.password)
    
    # Save user data
    user_data = {
        "username": user.username,
        "email": user.email,
        "mobileNo": user.mobileNo,  # Keep mobileNo for future use
        "hashed_password": hashed_password,
        "created_at": datetime.utcnow().isoformat(),
    }
    
    try:
        result = db["users"].insert_one(user_data)
        access_token = create_access_token(data={"sub": str(result.inserted_id)})
    except Exception as e:
        logger.error(f"Error registering user: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create user: {str(e)}")
    
    # Delete the verification record after successful registration
    db["verifications"].delete_one({"email": user.email})

    return {
        "id": str(result.inserted_id),
        "username": user.username,
        "email": user.email,
        "mobileNo": user.mobileNo,
        "created_at": user_data["created_at"],
        "access_token": access_token
    }

@router.post("/login", description="Login a user using username or email and password.")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """Login a user using username/email and password, using MongoDB."""
    try:
        client = get_db_client()
        db = client["jewelify"]
        user = db["users"].find_one({
            "$or": [
                {"username": form_data.username},
                {"email": form_data.username}
            ]
        })
    except Exception as e:
        logger.error(f"Database error during login: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    if not user or not verify_password(form_data.password, user["hashed_password"]):
        raise HTTPException(status_code=400, detail="Incorrect username/email or password")

    access_token = create_access_token(data={"sub": str(user["_id"])})
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me", description="Fetch details of the currently authenticated user.")
async def get_user_details(current_user: dict = Depends(get_current_user)):
    """Fetch the details of the currently authenticated user."""
    try:
        return {
            "id": str(current_user["_id"]),
            "username": current_user["username"],
            "email": current_user["email"],
            "mobileNo": current_user.get("mobileNo"),  # Keep mobileNo for future use
            "created_at": current_user["created_at"]
        }
    except Exception as e:
        logger.error(f"Error fetching user details: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch user details: {str(e)}")

# Commented out mobile number-based endpoints for future use
"""
# Function to check if a phone number is verified in Twilio
async def is_phone_number_verified(mobile_no: str) -> bool:
    try:
        # Fetch the list of verified caller IDs from Twilio
        caller_ids = twilio_client.validation_requests.list(phone_number=mobile_no)
        for caller_id in caller_ids:
            if caller_id.phone_number == mobile_no and caller_id.validation_code is None:
                return True  # Number is verified if it exists and has no pending validation code
        return False
    except TwilioRestException as e:
        logger.error(f"Twilio error while checking verified numbers: {e}")
        raise HTTPException(status_code=500, detail=f"Twilio error: {str(e)}")

@router.post("/verify-phone", description="Initiate phone number verification with Twilio in trial mode.")
@limiter.limit("5/minute")  # Limit to 5 requests per minute per IP
async def verify_phone(request: PhoneVerificationRequest, req: Request):
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN]):
        logger.error("Twilio credentials not found. Check environment variables.")
        raise HTTPException(status_code=500, detail="Twilio configuration error")

    mobile_no = request.mobileNo  # Already in E.164 format due to validator

    # Check if the number is already verified
    if await is_phone_number_verified(mobile_no):
        return {"message": "Phone number already verified"}

    try:
        # Initiate phone number verification with Twilio
        validation_request = twilio_client.validation_requests.create(
            phone_number=mobile_no,
            via="sms"  # Send verification code via SMS
        )
        logger.info(f"Phone verification initiated for {mobile_no}, friendly_name: {validation_request.friendly_name}")

        # Store the state in MongoDB
        client = get_db_client()
        db = client["jewelify"]
        db["otps"].delete_one({"mobileNo": mobile_no})  # Remove old record
        db["otps"].insert_one({
            "mobileNo": mobile_no,
            "state": "phone_verification_pending",
            "attempts": 0,
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": (datetime.utcnow() + timedelta(minutes=10)).isoformat()
        })

        return {"message": "Verification code sent to your phone number"}
    except TwilioRestException as e:
        logger.error(f"Twilio error while initiating phone verification: {e}")
        raise HTTPException(status_code=500, detail=f"Twilio error: {str(e)}")

@router.post("/verify-phone-code", description="Verify the phone number using the code sent by Twilio, then automatically send OTP.")
async def verify_phone_code(request: PhoneVerificationVerify):
    mobile_no = request.mobileNo  # Already in E.164 format due to validator

    # Check the current state
    client = get_db_client()
    db = client["jewelify"]
    otp_record = db["otps"].find_one({"mobileNo": mobile_no})
    if not otp_record or otp_record.get("state") != "phone_verification_pending":
        raise HTTPException(status_code=400, detail="Phone verification not initiated or already completed")

    # Check if the session has expired
    expires_at = datetime.fromisoformat(otp_record["expires_at"])
    if datetime.utcnow() > expires_at:
        db["otps"].delete_one({"mobileNo": mobile_no})
        raise HTTPException(status_code=400, detail="Phone verification session has expired")

    # Verify the phone number
    try:
        validation_request = twilio_client.validation_requests.create(
            phone_number=mobile_no,
            validation_code=request.code
        )
        if validation_request.validation_code is not None:
            raise HTTPException(status_code=400, detail="Invalid verification code")
    except TwilioRestException as e:
        logger.error(f"Twilio error while verifying phone code: {e}")
        raise HTTPException(status_code=500, detail=f"Twilio error: {str(e)}")

    # Phone number is verified, now send OTP
    try:
        verification = twilio_client.verify.services(TWILIO_VERIFY_SERVICE_SID).verifications.create(
            to=mobile_no,
            channel="sms"
        )
        logger.info(f"Phone number {mobile_no} verified, OTP sent via Twilio, sid: {verification.sid}")
    except TwilioRestException as e:
        logger.error(f"Twilio error while sending OTP: {e}")
        raise HTTPException(status_code=500, detail=f"Twilio error: {str(e)}")

    # Update the state in MongoDB
    try:
        db["otps"].delete_one({"mobileNo": mobile_no})  # Remove old record
        db["otps"].insert_one({
            "mobileNo": mobile_no,
            "state": "otp_verification_pending",
            "verification_sid": verification.sid,
            "attempts": 0,
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": (datetime.utcnow() + timedelta(minutes=10)).isoformat()
        })
    except Exception as e:
        logger.error(f"Error updating verification state: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    return {"message": "Phone number verified successfully, OTP sent to your phone"}

@router.post("/send-otp", description="Send OTP using Twilio Verify API, automatically verifying the phone number if needed.")
@limiter.limit("5/minute")  # Limit to 5 requests per minute per IP
async def send_otp(request: OtpRequest, req: Request):
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_VERIFY_SERVICE_SID]):
        logger.error("Twilio credentials not found. Check environment variables.")
        raise HTTPException(status_code=500, detail="Twilio configuration error")

    mobile_no = request.mobileNo  # Already in E.164 format due to validator

    # Check if the phone number is verified
    if not await is_phone_number_verified(mobile_no):
        # Initiate phone verification
        try:
            validation_request = twilio_client.validation_requests.create(
                phone_number=mobile_no,
                via="sms"
            )
            logger.info(f"Phone verification initiated for {mobile_no}, friendly_name: {validation_request.friendly_name}")
            
            # Store the state in MongoDB
            client = get_db_client()
            db = client["jewelify"]
            db["otps"].delete_one({"mobileNo": mobile_no})  # Remove old record
            db["otps"].insert_one({
                "mobileNo": mobile_no,
                "state": "phone_verification_pending",
                "attempts": 0,
                "created_at": datetime.utcnow().isoformat(),
                "expires_at": (datetime.utcnow() + timedelta(minutes=10)).isoformat()
            })
            return {"message": "Phone verification required. Please verify your phone number using /auth/verify-phone-code."}
        except TwilioRestException as e:
            logger.error(f"Twilio error while initiating phone verification: {e}")
            raise HTTPException(status_code=500, detail=f"Twilio error: {str(e)}")

    # Check for existing OTP session and retry attempts
    client = get_db_client()
    db = client["jewelify"]
    otp_record = db["otps"].find_one({"mobileNo": mobile_no})
    if otp_record and otp_record.get("state") == "otp_verification_pending":
        attempts = otp_record.get("attempts", 0)
        if attempts >= MAX_OTP_ATTEMPTS:
            cooldown_expires = datetime.fromisoformat(otp_record["created_at"]) + timedelta(minutes=OTP_COOLDOWN_MINUTES)
            if datetime.utcnow() < cooldown_expires:
                remaining_seconds = (cooldown_expires - datetime.utcnow()).total_seconds()
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many OTP attempts. Please wait {int(remaining_seconds)} seconds before trying again."
                )
            else:
                # Reset attempts after cooldown
                attempts = 0

        # Increment attempts
        db["otps"].update_one(
            {"mobileNo": mobile_no},
            {"$set": {"attempts": attempts + 1}}
        )

    # Send OTP
    try:
        verification = twilio_client.verify.services(TWILIO_VERIFY_SERVICE_SID).verifications.create(
            to=mobile_no,
            channel="sms"
        )
        logger.info(f"OTP sent to {mobile_no} via Twilio, sid: {verification.sid}")
    except TwilioRestException as e:
        logger.error(f"Twilio error while sending OTP: {e}")
        raise HTTPException(status_code=500, detail=f"Twilio error: {str(e)}")

    # Store verification SID in MongoDB
    try:
        db["otps"].delete_one({"mobileNo": mobile_no})  # Remove old record
        db["otps"].insert_one({
            "mobileNo": mobile_no,
            "state": "otp_verification_pending",
            "verification_sid": verification.sid,
            "attempts": 0,
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": (datetime.utcnow() + timedelta(minutes=10)).isoformat()
        })
    except Exception as e:
        logger.error(f"Error storing verification SID: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    return {"message": "OTP sent successfully"}

@router.post("/verify-otp", description="Verify the OTP using Twilio Verify API.")
@limiter.limit("5/minute")  # Limit to 5 requests per minute per IP
async def verify_otp(request: OtpVerify, req: Request):
    try:
        client = get_db_client()
        db = client["jewelify"]
        otp_record = db["otps"].find_one({"mobileNo": request.mobileNo})
        if not otp_record:
            raise HTTPException(status_code=400, detail="OTP session not found or expired")

        # Check if the session has expired
        expires_at = datetime.fromisoformat(otp_record["expires_at"])
        if datetime.utcnow() > expires_at:
            db["otps"].delete_one({"mobileNo": request.mobileNo})
            raise HTTPException(status_code=400, detail="OTP session has expired")

        # Verify the OTP using Twilio Verify API
        mobile_no = request.mobileNo  # Already in E.164 format due to validator
        verification_check = twilio_client.verify.services(TWILIO_VERIFY_SERVICE_SID).verification_checks.create(
            to=mobile_no,
            code=request.otp
        )

        if verification_check.status != "approved":
            logger.error(f"Twilio OTP verification failed: {verification_check.status}")
            raise HTTPException(status_code=400, detail="Invalid OTP")

        # OTP is valid, delete the session from the database
        db["otps"].delete_one({"mobileNo": request.mobileNo})
        logger.info(f"OTP verified successfully for {request.mobileNo}")
        return {"message": "OTP verified successfully"}
    except TwilioRestException as e:
        logger.error(f"Twilio error while verifying OTP: {e}")
        raise HTTPException(status_code=500, detail=f"Twilio error: {str(e)}")
    except Exception as e:
        logger.error(f"Error verifying OTP: {e}")
        raise HTTPException(status_code=500, detail=f"Error verifying OTP: {str(e)}")
"""