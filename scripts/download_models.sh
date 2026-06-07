#!/usr/bin/env bash
# Download Essentia TensorFlow models for audio analysis.
# Run from the project root: ./scripts/download_models.sh

set -euo pipefail

MODELS_DIR="$(cd "$(dirname "$0")/.." && pwd)/models"
BASE_URL="https://essentia.upf.edu/models"

mkdir -p "$MODELS_DIR"

echo "Downloading models to $MODELS_DIR..."

# Feature extractor (embeddings)
echo "  → discogs-effnet-bs64-1.pb"
curl -sL "$BASE_URL/feature-extractors/discogs-effnet/discogs-effnet-bs64-1.pb" \
    -o "$MODELS_DIR/discogs-effnet-bs64-1.pb"

# Classification heads
CLASSIFIERS=(
    "mood_aggressive"
    "mood_happy"
    "mood_relaxed"
    "mood_sad"
    "mood_party"
    "voice_instrumental"
    "danceability"
)

for name in "${CLASSIFIERS[@]}"; do
    echo "  → ${name}-discogs-effnet-1.pb"
    curl -sL "$BASE_URL/classification-heads/$name/${name}-discogs-effnet-1.pb" \
        -o "$MODELS_DIR/${name}-discogs-effnet-1.pb"
    curl -sL "$BASE_URL/classification-heads/$name/${name}-discogs-effnet-1.json" \
        -o "$MODELS_DIR/${name}-discogs-effnet-1.json"
done

echo "Done. $(ls "$MODELS_DIR"/*.pb | wc -l) models downloaded."
