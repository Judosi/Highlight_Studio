"""Local face locations using MediaPipe Tasks and the bundled Apache-2.0 model.

No identity recognition and no downloads/uploads during a render. Reading one
RGB frame at a time keeps memory bounded for long Shorts on 16 GB machines.
"""
from pathlib import Path
from typing import Callable

MODEL_PATH = Path(__file__).resolve().parent.parent / 'assets' / 'face_tracking' / 'blaze_face_short_range.tflite'


def detect_faces(raw_path: Path, *, width: int, height: int, fps: float,
                 cancelled: Callable[[], None] | None = None) -> list[dict]:
    import numpy as np
    import mediapipe as mp

    if not MODEL_PATH.is_file():
        raise RuntimeError('Модель лиц отсутствует. Распакуйте полный архив приложения.')
    frame_size = width * height * 3
    if frame_size <= 0 or fps <= 0:
        raise ValueError('Некорректный размер кадров или частота анализа')
    options = mp.tasks.vision.FaceDetectorOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(MODEL_PATH),
                                        delegate=mp.tasks.BaseOptions.Delegate.CPU),
        running_mode=mp.tasks.vision.RunningMode.VIDEO,
        min_detection_confidence=0.48,
    )
    points = []
    with mp.tasks.vision.FaceDetector.create_from_options(options) as detector, raw_path.open('rb') as stream:
        frame_index = 0
        while True:
            if cancelled:
                cancelled()
            payload = stream.read(frame_size)
            if len(payload) < frame_size:
                break
            frame = np.frombuffer(payload, dtype=np.uint8).reshape((height, width, 3))
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame)
            result = detector.detect_for_video(image, round(frame_index * 1000 / fps))
            for detection in result.detections:
                bbox = detection.bounding_box
                bw = min(1.0, max(0.0, bbox.width / width))
                bh = min(1.0, max(0.0, bbox.height / height))
                if bw <= .005 or bh <= .005:
                    continue
                points.append({
                    'frame': frame_index, 't': round(frame_index / fps, 3),
                    'cx': round(min(1.0, max(0.0, (bbox.origin_x + bbox.width/2) / width)), 5),
                    'cy': round(min(1.0, max(0.0, (bbox.origin_y + bbox.height/2) / height)), 5),
                    'w': round(bw, 5), 'h': round(bh, 5),
                    'score': round(detection.categories[0].score, 4) if detection.categories else .5,
                })
            frame_index += 1
    return points
