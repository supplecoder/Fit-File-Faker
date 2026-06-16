"""FORM swim goggles → Garmin Connect sync pipeline.

This subpackage monitors a Gmail inbox for FORM export emails, downloads
the FIT file archive, processes it through fit-file-faker to simulate a
Garmin device, and uploads the result to Garmin Connect.

Designed to run as a GitHub Actions scheduled workflow. All credentials
are read from environment variables (populated from GitHub Secrets).

Modules:
    gmail      -- IMAP connection, email search, S3 URL extraction
    downloader -- ZIP download from S3, FIT file extraction
    processor  -- fit-file-faker integration (device rewrite)
    garmin     -- Garmin Connect upload and session token management
    github     -- GitHub Secrets API (token persistence between runs)
    pipeline   -- Main orchestrator; entry point for the workflow
"""
