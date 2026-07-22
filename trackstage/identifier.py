"""identifier.py — Discogs release identification for a single track.

Thin orchestration over pipeline's existing scoring + metadata extraction.
"""

from .pipeline import (
    extract_meta, score_track, score_by_tracklist,
    DEFAULT_THRESHOLD, REVIEW_THRESHOLD,
)


def identify(client, artist, title, threshold=DEFAULT_THRESHOLD,
             discogs_id=None):
    """Return (meta, confidence). meta is None if nothing clears REVIEW_THRESHOLD.

    Mirrors pipeline.process_file's two-pass logic without side effects.
    """
    if discogs_id:
        release = client.get_release(discogs_id)
        if release:
            return extract_meta(release), 100
        return None, 0

    results = client.search(f"{artist} {title}")
    if not results:
        return None, 0

    scores = [score_track(r, artist, title) for r in results]
    top = scores[0]

    if top >= threshold:
        release = client.get_release(results[0]["id"])
        return (extract_meta(release), top) if release else (None, 0)

    if top < REVIEW_THRESHOLD:
        return None, top

    # Pass 2: tracklist check on top 3
    best_release, best_score = None, 0
    for result in results[:3]:
        release = client.get_release(result["id"])
        if not release:
            continue
        tl = score_by_tracklist(release, title)
        if tl > best_score:
            best_score, best_release = tl, release

    if best_release and best_score >= REVIEW_THRESHOLD:
        return extract_meta(best_release), best_score
    return None, best_score
