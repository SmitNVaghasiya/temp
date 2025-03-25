from fastapi import APIRouter, File, UploadFile, HTTPException, Depends, Form
from services.predictor import get_predictor, predict_compatibility
from services.database import save_prediction, get_prediction_by_id
from api.dependencies import get_current_user
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/predictions", tags=["predictions"])
predictor = get_predictor(
    os.getenv("MODEL_PATH", "rl_jewelry_model.keras"),
    os.getenv("SCALER_PATH", "scaler.pkl"),
    os.getenv("PAIRWISE_FEATURES_PATH", "pairwise_features.npy")
)

@router.post("/predict")
async def predict(
    face: UploadFile = File(...),
    jewelry: UploadFile = File(...),
    face_image_path: str = Form(...),
    jewelry_image_path: str = Form(...),
    current_user: dict = Depends(get_current_user)
):
    global predictor
    if predictor is None:
        predictor = get_predictor(
            os.getenv("MODEL_PATH", "rl_jewelry_model.keras"),
            os.getenv("SCALER_PATH", "scaler.pkl"),
            os.getenv("PAIRWISE_FEATURES_PATH", "pairwise_features.npy")
        )
        if predictor is None:
            raise HTTPException(status_code=500, detail="Model is not loaded properly")

    # Log the content types of the uploaded files
    logger.info(f"Received face file with content_type: {face.content_type}")
    logger.info(f"Received jewelry file with content_type: {jewelry.content_type}")

    # Check if the content type is an image or a fallback type with a valid image extension
    valid_image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp'}
    face_extension = os.path.splitext(face.filename)[1].lower()
    jewelry_extension = os.path.splitext(jewelry.filename)[1].lower()

    is_face_valid = (
        face.content_type.startswith('image/') or
        (face.content_type == 'application/octet-stream' and face_extension in valid_image_extensions)
    )
    is_jewelry_valid = (
        jewelry.content_type.startswith('image/') or
        (jewelry.content_type == 'application/octet-stream' and jewelry_extension in valid_image_extensions)
    )

    if not is_face_valid or not is_jewelry_valid:
        raise HTTPException(
            status_code=400,
            detail=f"Uploaded files must be images. Face content_type: {face.content_type}, Jewelry content_type: {jewelry.content_type}"
        )

    try:
        face_data = await face.read()
        jewelry_data = await jewelry.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read uploaded images: {str(e)}")

    try:
        score, category, recommendations = predict_compatibility(predictor, face_data, jewelry_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prediction error: {str(e)}")
    
    if score is None:
        raise HTTPException(status_code=500, detail="Prediction failed")

    try:
        prediction_id = save_prediction(
            score=score,
            category=category,
            recommendations=recommendations,
            user_id=str(current_user["_id"]),
            face_image_path=face_image_path,
            jewelry_image_path=jewelry_image_path
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save prediction: {str(e)}")
    
    return {
        "prediction_id": prediction_id,
        "score": score,
        "category": category,
        "recommendations": recommendations,
        "face_image_path": face_image_path,
        "jewelry_image_path": jewelry_image_path
    }

@router.get("/get_prediction/{prediction_id}")
async def get_prediction(
    prediction_id: str,
    current_user: dict = Depends(get_current_user)
):
    try:
        result = get_prediction_by_id(prediction_id, str(current_user["_id"]))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    
    if "error" in result:
        status_code = 404 if result["error"] == "Prediction not found" else 500
        raise HTTPException(status_code=status_code, detail=result["error"])
    return result