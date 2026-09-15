"""Bagdrop price engine.

Deliberately not empty: GitHub's web uploader silently skips zero-byte files,
which drops this one and breaks `python -m app.ingest` with a confusing
"No module named 'app'".
"""
