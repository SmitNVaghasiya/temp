from bson import ObjectId
from pydantic import BaseModel, Field, field_validator, EmailStr

class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr = Field(...)
    password: str = Field(..., min_length=6)
    verification_code: str = Field(..., min_length=6, max_length=6)
    mobileNo: str | None = None  # Keep for future use

class UserLogin(BaseModel):
    username_or_email: str = Field(..., alias="username")
    password: str = Field(...)

class UserOut(BaseModel):
    id: str
    username: str
    email: str
    mobileNo: str | None  # Keep for future use
    created_at: str

    class Config:
        from_attributes = True
        populate_by_name = True
        json_encoders = {ObjectId: str}