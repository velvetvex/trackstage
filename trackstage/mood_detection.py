"""
mood_detection.py — ML-based mood/vibe tagging and vocal detection.

Uses Essentia TensorFlow models (discogs-effnet embeddings + classification heads).
Returns: mood tags (aggressive, happy, relaxed, sad, party), vocal/instrumental flag.
"""

import json
import logging
import os
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

MODELS_DIR = Path(__file__).parent.parent / "models"

CLASSIFIERS = [
    "mood_aggressive",
    "mood_happy",
    "mood_relaxed",
    "mood_sad",
    "mood_party",
    "voice_instrumental",
]

VIBE_MAP = {
    "aggressive": "dark",
    "happy": "euphoric",
    "relaxed": "deep",
    "sad": "melancholic",
    "party": "driving",
}

# Vibes that only apply when energy is high enough
ENERGY_GATED_VIBES = {"driving": 5}

_embedding_model = None
_classifier_cache = {}


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from essentia.standard import TensorflowPredictEffnetDiscogs
        _embedding_model = TensorflowPredictEffnetDiscogs(
            graphFilename=str(MODELS_DIR / "discogs-effnet-bs64-1.pb"),
            output="PartitionedCall:1",
        )
    return _embedding_model


def _get_classifier(name):
    if name not in _classifier_cache:
        from essentia.standard import TensorflowPredict2D
        pb = MODELS_DIR / f"{name}-discogs-effnet-1.pb"
        js = MODELS_DIR / f"{name}-discogs-effnet-1.json"
        if not pb.exists() or not js.exists():
            return None, None
        with open(js) as f:
            meta = json.load(f)
        model = TensorflowPredict2D(
            graphFilename=str(pb),
            output="model/Softmax",
        )
        _classifier_cache[name] = (model, meta["classes"])
    return _classifier_cache[name]


CLASSIFIER_THRESHOLDS = {
    "mood_aggressive": 0.55,
    "mood_happy": 0.55,
    "mood_relaxed": 0.55,
    "mood_sad": 0.55,
    "mood_party": 0.70,
}


def detect_mood(file_path: Path, confidence_threshold: float = 0.55, energy: int = 5) -> dict:
    """Classify mood and vocal/instrumental for a track.

    energy: track energy (1-10) from audio_analysis, used to gate certain vibes.
    """
    result = {
        "moods": [],
        "vibes": [],
        "vocal": "",
    }

    try:
        from essentia.standard import MonoLoader
        audio = MonoLoader(filename=str(file_path), sampleRate=16000, resampleQuality=4)()
    except Exception as e:
        log.warning(f"  ⚠  Could not load audio for mood: {e}")
        return result

    try:
        embeddings = _get_embedding_model()(audio)
    except Exception as e:
        log.warning(f"  ⚠  Embedding extraction failed: {e}")
        return result

    for clf_name in CLASSIFIERS:
        clf = _get_classifier(clf_name)
        if clf[0] is None:
            continue
        model, classes = clf

        try:
            preds = model(embeddings)
            avg = np.mean(preds, axis=0)
            top_idx = np.argmax(avg)
            top_class = classes[top_idx]
            confidence = avg[top_idx]

            if clf_name == "voice_instrumental":
                if confidence > 0.5:
                    result["vocal"] = top_class
                else:
                    result["vocal"] = "instrumental"
            elif confidence >= CLASSIFIER_THRESHOLDS.get(clf_name, confidence_threshold):
                if "not_" not in top_class and "non_" not in top_class:
                    mood = top_class
                    vibe = VIBE_MAP.get(mood, mood)
                    # Gate vibes that need minimum energy
                    min_energy = ENERGY_GATED_VIBES.get(vibe)
                    if min_energy and energy < min_energy:
                        continue
                    result["moods"].append(mood)
                    result["vibes"].append(vibe)
        except Exception as e:
            log.warning(f"  ⚠  Classifier {clf_name} failed: {e}")

    return result


def format_mood_log(mood: dict) -> str:
    parts = []
    if mood["vocal"]:
        parts.append(mood["vocal"].capitalize())
    if mood["vibes"]:
        parts.append("Vibe: " + ", ".join(mood["vibes"]))
    return "  │  ".join(parts) if parts else "No mood data"
