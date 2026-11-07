"""Local CPU landmark detection. Never sends images to an external service."""
import io
import math
import os
import threading
import warnings
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

engine = None
inference_lock = threading.Lock()
MODEL = 'MediaPipe Pose Landmarker Lite'
MODEL_VERSION = 'mediapipe-1.2.2/lite-float16-v1/59929e1d1ee9'


def select_landmarks(result, side):
    if not result.pose_landmarks:
        raise HTTPException(422, {'code': 'no_pose', 'message': 'No full-body pose was found. For a cropped leg photo, align the thigh and shin arms manually using the visible leg axes.'})
    if len(result.pose_landmarks) > 1:
        raise HTTPException(422, {'code': 'multiple_people', 'message': 'More than one person was detected. Crop out other people and retry, or align the arms manually.'})
    indices = (23, 25, 27) if side == 'left' else (24, 26, 28)
    landmarks = [result.pose_landmarks[0][i] for i in indices]
    confidence = min(min(p.visibility or 0, p.presence or 0) for p in landmarks)
    if confidence < 0.65 or any(not math.isfinite(p.x) or not math.isfinite(p.y) or not 0 <= p.x <= 1 or not 0 <= p.y <= 1 for p in landmarks):
        raise HTTPException(422, {'code': 'hidden_landmarks', 'message': 'The selected hip, knee and ankle could not all be located confidently. Align the thigh and shin arms manually; a hidden hip is not required for manual axis alignment.'})
    return {'points': [{'x': p.x, 'y': p.y} for p in landmarks], 'confidence': confidence,
            'model': MODEL, 'model_version': MODEL_VERSION}


@asynccontextmanager
async def lifespan(app):
    global engine
    import mediapipe as mp
    options = mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=os.environ.get('POSE_MODEL_PATH', '/models/pose_landmarker_lite.task'),
                                         delegate=mp.tasks.BaseOptions.Delegate.CPU),
        running_mode=mp.tasks.vision.RunningMode.IMAGE, num_poses=2,
        min_pose_detection_confidence=0.5, min_pose_presence_confidence=0.5)
    engine = mp.tasks.vision.PoseLandmarker.create_from_options(options)
    yield
    engine.close()
    engine = None


app = FastAPI(title='Stride local pose analysis', lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get('/health')
def health():
    if engine is None:
        raise HTTPException(503, 'The local model is not ready.')
    return {'status': 'ok', 'model': MODEL, 'model_version': MODEL_VERSION}


@app.post('/analyze')
def analyze(file: UploadFile = File(...), side: str = Form(...)):
    if side not in ('left', 'right'):
        raise HTTPException(422, 'Choose the left or right leg.')
    if engine is None:
        raise HTTPException(503, 'The local model is not ready.')
    content = file.file.read(12 * 1024 * 1024 + 1)
    if len(content) > 12 * 1024 * 1024:
        raise HTTPException(413, 'Image is too large.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as original:
                image = original.convert('RGB')
                image.thumbnail((2400, 2400))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise HTTPException(422, 'The image could not be read.')
    import mediapipe as mp
    import numpy as np
    with inference_lock:
        result = engine.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.asarray(image)))
    return select_landmarks(result, side)
