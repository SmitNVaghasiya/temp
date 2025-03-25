import os
import numpy as np
import tensorflow as tf
from tensorflow.keras.applications import MobileNetV2
from tensorflow.keras.preprocessing import image
from tensorflow.keras.applications.mobilenet_v2 import preprocess_input
from tensorflow.keras.models import load_model, Model
import pickle
from io import BytesIO
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class JewelryRLPredictor:
    def __init__(self, model_path, scaler_path, pairwise_features_path):
        for path in [model_path, scaler_path, pairwise_features_path]:
            if not os.path.exists(path):
                raise FileNotFoundError(f"Missing required file: {path}")

        logger.info("🚀 Loading model...")
        self.model = load_model(model_path)
        self.img_size = (224, 224)
        self.feature_size = 1280
        self.device = "/GPU:0" if tf.config.list_physical_devices('GPU') else "/CPU:0"

        logger.info("📏 Loading scaler...")
        with open(scaler_path, 'rb') as f:
            self.scaler = pickle.load(f)

        logger.info("🔄 Setting up MobileNetV2 feature extractor...")
        base_model = MobileNetV2(weights="imagenet", include_top=False, input_shape=(224, 224, 3))
        global_avg_layer = tf.keras.layers.GlobalAveragePooling2D()
        reduction_layer = tf.keras.layers.Dense(self.feature_size, activation="relu")
        self.feature_extractor = Model(
            inputs=base_model.input,
            outputs=reduction_layer(global_avg_layer(base_model.output))
        )

        logger.info("📂 Loading pairwise features...")
        self.pairwise_features = np.load(pairwise_features_path, allow_pickle=True).item()
        self.pairwise_features = {
            k: self.scaler.transform(np.array(v).reshape(1, -1))
            for k, v in self.pairwise_features.items() if v is not None and v.size == self.feature_size
        }
        self.jewelry_list = list(self.pairwise_features.values())
        self.jewelry_names = list(self.pairwise_features.keys())
        logger.info(f"✅ Predictor initialized successfully with {len(self.jewelry_names)} jewelry items!")

    def extract_features(self, img_data):
        if not img_data:
            logger.error("❌ Image data is empty")
            return None

        try:
            img = image.load_img(BytesIO(img_data), target_size=self.img_size)
            img_array = image.img_to_array(img)
            img_array = np.expand_dims(img_array, axis=0)
            img_array = preprocess_input(img_array)
            features = self.feature_extractor.predict(img_array, verbose=0)
            return self.scaler.transform(features)
        except Exception as e:
            logger.error(f"❌ Error extracting features: {str(e)}")
            return None

    def predict_compatibility(self, face_data, jewel_data):
        face_features = self.extract_features(face_data)
        jewel_features = self.extract_features(jewel_data)
        if face_features is None or jewel_features is None:
            logger.error("Feature extraction failed for face or jewelry image")
            return None, "Feature extraction failed", []

        face_norm = face_features / np.linalg.norm(face_features, axis=1, keepdims=True)
        jewel_norm = jewel_features / np.linalg.norm(jewel_features, axis=1, keepdims=True)
        cosine_similarity = np.sum(face_norm * jewel_norm, axis=1)[0]
        scaled_score = (cosine_similarity + 1) / 2.0

        if scaled_score >= 0.8:
            category = "Very Good"
        elif scaled_score >= 0.6:
            category = "Good"
        elif scaled_score >= 0.4:
            category = "Neutral"
        elif scaled_score >= 0.2:
            category = "Bad"
        else:
            category = "Very Bad"

        logger.info(f"Computed compatibility score: {scaled_score}, Category: {category}")
        with tf.device(self.device):
            q_values = self.model.predict(face_features, verbose=0)[0]

        if len(q_values) != len(self.jewelry_names):
            logger.error(f"Q-values length ({len(q_values)}) does not match jewelry list ({len(self.jewelry_names)})")
            return scaled_score, category, []

        # Normalize Q-values to a 0-1 scale and compute categories for recommendations
        q_min, q_max = np.min(q_values), np.max(q_values)
        if q_max == q_min:
            logger.warning("All Q-values are the same, setting normalized scores to 0.5")
            q_values_normalized = np.full_like(q_values, 0.5)
        else:
            q_values_normalized = (q_values - q_min) / (q_max - q_min)

        top_indices = np.argsort(q_values)[::-1]
        recommendations = []
        for idx in top_indices[:10]:
            score = q_values_normalized[idx]
            # Compute category based on the normalized score
            if score >= 0.8:
                rec_category = "Very Good"
            elif score >= 0.6:
                rec_category = "Good"
            elif score >= 0.4:
                rec_category = "Neutral"
            elif score >= 0.2:
                rec_category = "Bad"
            else:
                rec_category = "Very Bad"
            recommendations.append({
                "name": self.jewelry_names[idx],
                "score": float(score),  # Convert to float for JSON serialization
                "category": rec_category
            })
        logger.info(f"Generated {len(recommendations)} recommendations")
        return scaled_score, category, recommendations

def get_predictor(model_path, scaler_path, pairwise_features_path):
    try:
        predictor = JewelryRLPredictor(model_path, scaler_path, pairwise_features_path)
        return predictor
    except Exception as e:
        logger.error(f"🚨 Failed to initialize JewelryRLPredictor: {str(e)}")
        return None

def predict_compatibility(predictor, face_data, jewelry_data):
    if predictor is None:
        logger.error("Predictor not initialized")
        return None, "Predictor not initialized", []
    return predictor.predict_compatibility(face_data, jewelry_data)