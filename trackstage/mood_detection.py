"""
mood_detection.py — ML-based mood/vibe tagging and vocal detection.

Uses Essentia mel spectrograms + TensorFlow frozen graphs (discogs-effnet
embeddings + classification heads). Does NOT require essentia-tensorflow's
TensorflowPredict* algorithms — runs inference via raw tf.compat.v1.Session.
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

ENERGY_GATED_VIBES = {"driving": 5}

CLASSIFIER_THRESHOLDS = {
    "mood_aggressive": 0.55,
    "mood_happy": 0.55,
    "mood_relaxed": 0.55,
    "mood_sad": 0.55,
    "mood_party": 0.70,
}

_embedding_session = None
_embedding_graph = None
_classifier_cache = {}


def _compute_mel_patches(audio: np.ndarray) -> np.ndarray:
    from essentia.standard import Windowing, Spectrum, MelBands

    windowing = Windowing(type='hann', size=512, zeroPadding=0)
    spectrum = Spectrum(size=512)
    melbands = MelBands(
        numberBands=96, sampleRate=16000,
        lowFrequencyBound=0, highFrequencyBound=8000,
    )

    hop = 256
    frames = []
    for i in range(0, len(audio) - 512, hop):
        w = windowing(audio[i:i + 512])
        s = spectrum(w)
        m = melbands(s)
        frames.append(m)

    mel_spec = np.log1p(np.array(frames, dtype=np.float32) * 10000)

    patch_size = 128
    n_patches = mel_spec.shape[0] // patch_size
    if n_patches == 0:
        return np.empty((0, 128, 96), dtype=np.float32)

    patches = np.array([
        mel_spec[i * patch_size:(i + 1) * patch_size]
        for i in range(n_patches)
    ], dtype=np.float32)
    return patches


def _get_embedding_session():
    global _embedding_session, _embedding_graph
    if _embedding_session is None:
        import tensorflow as tf
        _embedding_graph = tf.Graph()
        with _embedding_graph.as_default():
            graph_def = tf.compat.v1.GraphDef()
            pb_path = MODELS_DIR / "discogs-effnet-bs64-1.pb"
            with open(pb_path, 'rb') as f:
                graph_def.ParseFromString(f.read())
            tf.import_graph_def(graph_def, name='')
        _embedding_session = tf.compat.v1.Session(graph=_embedding_graph)
    return _embedding_session, _embedding_graph


def _compute_embeddings(patches: np.ndarray) -> np.ndarray:
    sess, graph = _get_embedding_session()
    input_t = graph.get_tensor_by_name('serving_default_melspectrogram:0')
    output_t = graph.get_tensor_by_name('PartitionedCall:1')

    batch_size = 64
    all_embeddings = []
    for i in range(0, len(patches), batch_size):
        batch = patches[i:i + batch_size]
        real_count = len(batch)
        if real_count < batch_size:
            pad = np.zeros((batch_size - real_count, 128, 96), dtype=np.float32)
            batch = np.concatenate([batch, pad], axis=0)
        emb = sess.run(output_t, feed_dict={input_t: batch})
        all_embeddings.append(emb[:real_count])

    return np.concatenate(all_embeddings, axis=0)


def _get_classifier(name: str):
    if name not in _classifier_cache:
        import tensorflow as tf
        pb = MODELS_DIR / f"{name}-discogs-effnet-1.pb"
        js = MODELS_DIR / f"{name}-discogs-effnet-1.json"
        if not pb.exists() or not js.exists():
            _classifier_cache[name] = (None, None, None)
            return _classifier_cache[name]

        with open(js) as f:
            meta = json.load(f)

        graph = tf.Graph()
        with graph.as_default():
            graph_def = tf.compat.v1.GraphDef()
            with open(pb, 'rb') as f2:
                graph_def.ParseFromString(f2.read())
            tf.import_graph_def(graph_def, name='')
        sess = tf.compat.v1.Session(graph=graph)
        _classifier_cache[name] = (sess, graph, meta["classes"])

    return _classifier_cache[name]


def detect_mood(file_path: Path, confidence_threshold: float = 0.55, energy: int = 5) -> dict:
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
        patches = _compute_mel_patches(audio)
        if len(patches) == 0:
            log.warning("  ⚠  Track too short for mood detection")
            return result
        embeddings = _compute_embeddings(patches)
    except Exception as e:
        log.warning(f"  ⚠  Embedding extraction failed: {e}")
        return result

    for clf_name in CLASSIFIERS:
        sess, graph, classes = _get_classifier(clf_name)
        if sess is None:
            continue

        try:
            preds = sess.run(
                graph.get_tensor_by_name('model/Softmax:0'),
                feed_dict={graph.get_tensor_by_name('model/Placeholder:0'): embeddings},
            )
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
