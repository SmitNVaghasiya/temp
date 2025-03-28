# jewelify_server

Deploying on Render
When deploying on Render, ensure that:

Environment Variables:
Set the same variables from your .env file in the Render dashboard.

Port Configuration:
Render provides a dynamic $PORT environment variable. Your code in main.py reads the port from the environment, so no changes are needed.

Model Files:
Make sure to include your model files (rl_jewelry_model.keras, scaler.pkl, and pairwise_features.npy) in the deployment package.

Dependencies:
Render will use your requirements.txt to install dependencies.

Enabling Twilio OTP Functionality
Currently, the code for sending OTP via Twilio is commented out in api/routes/auth.py. To enable Twilio:

Edit api/routes/auth.py:

Locate the commented-out block within the /register endpoint:

# ----- Twilio OTP integration (commented out) -----

"""
from twilio.rest import Client
try:
twilio*client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
message = twilio_client.messages.create(
body=f"Your OTP is {user.otp}",
from*=os.environ["TWILIO_PHONE_NUMBER"],
to=user.mobileNo
)
except Exception as e:
raise HTTPException(status_code=500, detail="Twilio OTP sending failed: " + str(e))
"""

# --------------------------------------------------

Uncomment the Twilio Block:

Remove the triple quotes (""") around the Twilio code so that it becomes active.

Update .env File:

Make sure the following variables in your .env file have valid Twilio credentials:

TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_PHONE_NUMBER=your_twilio_phone_number

Restart the Server:

After saving the changes, restart your server to enable OTP via Twilio.
