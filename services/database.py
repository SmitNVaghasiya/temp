from pymongo import MongoClient
import os
from datetime import datetime
from dotenv import load_dotenv
import logging
from bson import ObjectId

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)

def get_db_client():
    global client
    if client is None:
        try:
            # Mask the password in the URI for logging
            uri_for_logging = MONGO_URI
            if "://" in MONGO_URI:
                parts = MONGO_URI.split("://")
                user_pass = parts[1].split("@")[0]
                masked_user_pass = user_pass.split(":")[0] + ":****"
                uri_for_logging = parts[0] + "://" + masked_user_pass + "@" + MONGO_URI.split("@")[1]
            logger.info(f"Attempting to connect to MongoDB with URI: {uri_for_logging}")
            client = MongoClient(MONGO_URI)
            client.admin.command('ping')
            logger.info("✅ Successfully connected to MongoDB Atlas!")
        except Exception as e:
            logger.error(f"🚨 Failed to connect to MongoDB Atlas: {e}")
            client = None
    else:
        logger.info("MongoDB client already initialized")
    return client

def rebuild_client():
    global client, MONGO_URI
    if not MONGO_URI:
        logger.error("🚨 Cannot rebuild client: MONGO_URI not found")
        return False
    try:
        # Mask the password in the URI for logging
        uri_for_logging = MONGO_URI
        if "://" in MONGO_URI:
            parts = MONGO_URI.split("://")
            user_pass = parts[1].split("@")[0]
            masked_user_pass = user_pass.split(":")[0] + ":****"
            uri_for_logging = parts[0] + "://" + masked_user_pass + "@" + MONGO_URI.split("@")[1]
        logger.info(f"Rebuilding MongoDB client with URI: {uri_for_logging}")
        client = MongoClient(MONGO_URI)
        client.admin.command('ping')
        logger.info("✅ Successfully rebuilt MongoDB client")
        return True
    except Exception as e:
        logger.error(f"🚨 Failed to rebuild MongoDB client: {e}")
        return False

def save_prediction(score, category, recommendations, user_id, face_image_path, jewelry_image_path):
    client = get_db_client()
    if not client:
        logger.warning("⚠️ No MongoDB client available, attempting to rebuild")
        if not rebuild_client():
            logger.error("❌ Failed to rebuild MongoDB client, cannot save prediction")
            raise Exception("Failed to rebuild MongoDB client")

    try:
        db = client["jewelify"]
        users_collection = db["users"]
        recommendations_collection = db["recommendations"]

        # Fetch the user's email based on user_id
        user = users_collection.find_one({"_id": ObjectId(user_id)})
        if not user:
            logger.error(f"User with ID {user_id} not found")
            raise Exception(f"User with ID {user_id} not found")

        email = user.get("email")
        mobileNo = user.get("mobileNo")  # Keep mobileNo for future use

        prediction = {
            "user_id": ObjectId(user_id),
            "email": email,  # Store the email
            "mobileNo": mobileNo,  # Store mobileNo for future use (optional)
            "score": score,
            "category": category,
            "recommendations": recommendations,
            "face_image_path": face_image_path,  # Store the local path
            "jewelry_image_path": jewelry_image_path,  # Store the local path
            "timestamp": datetime.utcnow().isoformat()
        }
        result = recommendations_collection.insert_one(prediction)
        logger.info(f"✅ Saved prediction with ID: {result.inserted_id}")
        return str(result.inserted_id)
    except Exception as e:
        logger.error(f"❌ Error saving prediction to MongoDB: {e}")
        raise Exception(f"Error saving prediction to MongoDB: {str(e)}")

def get_prediction_by_id(prediction_id, user_id):
    client = get_db_client()
    if not client:
        logger.warning("⚠️ No MongoDB client available, attempting to rebuild")
        if not rebuild_client():
            logger.error("❌ Failed to rebuild MongoDB client, cannot retrieve prediction")
            raise Exception("Failed to rebuild MongoDB client")

    try:
        db = client["jewelify"]
        predictions_collection = db["recommendations"]
        images_collection = db["images"]

        prediction = predictions_collection.find_one({
            "_id": ObjectId(prediction_id),
            "user_id": ObjectId(user_id)
        })
        if not prediction:
            logger.warning(f"⚠️ Prediction with ID {prediction_id} not found for user {user_id}")
            raise Exception("Prediction not found")

        recommendations = prediction.get("recommendations", [])
        image_data = []
        for rec in recommendations:
            # Ensure 'name' exists in the recommendation
            name = rec.get("name") if isinstance(rec, dict) else rec
            if not name:
                continue
            image_doc = images_collection.find_one({"name": name})
            url = image_doc.get("url") if image_doc and "url" in image_doc else None
            image_data.append({
                "name": name,
                "url": url
            })

        result = {
            "id": str(prediction["_id"]),
            "email": prediction["email"],  # Include email
            "mobileNo": prediction.get("mobileNo"),  # Include mobileNo for future use (optional)
            "score": prediction["score"],
            "category": prediction["category"],
            "recommendations": image_data,
            "face_image_path": prediction.get("face_image_path"),
            "jewelry_image_path": prediction.get("jewelry_image_path"),
            "timestamp": prediction["timestamp"]
        }
        logger.info(f"✅ Retrieved prediction with ID: {prediction_id} for user {user_id}")
        return result
    except Exception as e:
        logger.error(f"❌ Error retrieving prediction from MongoDB: {e}")
        raise Exception(f"Error retrieving prediction from MongoDB: {str(e)}")

def get_user_predictions(user_id):
    client = get_db_client()
    if not client:
        logger.warning("⚠️ No MongoDB client available, attempting to rebuild")
        if not rebuild_client():
            logger.error("❌ Failed to rebuild MongoDB client, cannot retrieve predictions")
            raise Exception("Failed to rebuild MongoDB client")

    try:
        db = client["jewelify"]
        predictions_collection = db["recommendations"]
        images_collection = db["images"]

        predictions = list(predictions_collection.find({"user_id": ObjectId(user_id)}).sort("timestamp", -1))
        if not predictions:
            logger.warning(f"⚠️ No predictions found for user {user_id}")
            raise Exception("No predictions found")

        results = []
        for prediction in predictions:
            recommendations = prediction.get("recommendations", [])
            image_data = []
            for rec in recommendations:
                # Ensure 'name' exists in the recommendation
                name = rec.get("name") if isinstance(rec, dict) else rec
                if not name:
                    continue
                image_doc = images_collection.find_one({"name": name})
                url = image_doc.get("url") if image_doc and "url" in image_doc else None
                image_data.append({
                    "name": name,
                    "url": url
                })

            results.append({
                "id": str(prediction["_id"]),
                "email": prediction["email"],  # Include email
                "mobileNo": prediction.get("mobileNo"),  # Include mobileNo for future use (optional)
                "score": prediction["score"],
                "category": prediction["category"],
                "recommendations": image_data,
                "face_image_path": prediction.get("face_image_path"),
                "jewelry_image_path": prediction.get("jewelry_image_path"),
                "timestamp": prediction["timestamp"]
            })

        logger.info(f"✅ Retrieved {len(results)} predictions for user {user_id}")
        return results
    except Exception as e:
        logger.error(f"❌ Error retrieving predictions from MongoDB: {e}")
        raise Exception(f"Error retrieving predictions from MongoDB: {str(e)}")