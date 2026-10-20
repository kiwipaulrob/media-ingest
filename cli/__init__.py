"""Stage pipeline CLIs for media ingest.

Each module is a thin, independently runnable stage:
    scan   -> read the downloader queue, probe candidates          (read-only)
    plan   -> decide REPLACE vs human-check per candidate          (read-only)
    apply  -> execute explicitly approved plan ids                 (destructive)
    verify -> report the 4-field exit gate                         (read-only)
"""
