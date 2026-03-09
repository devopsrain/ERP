"""
AWS Secrets Manager loader — injects secrets into os.environ at app startup.

Falls back silently if:
  - boto3 is not installed (local dev without AWS SDK)
  - The instance has no IAM role / credentials (running outside AWS)
  - The secret does not exist yet (first deployment before provisioning)

Local .env values and explicit environment variables always win — this
loader only sets keys that are not already present in os.environ.

Usage in app.py (call before Flask reads any env var):
    from secrets_loader import load_secrets
    load_secrets()
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

# The secret name in AWS Secrets Manager (override with env var)
_SECRET_NAME = os.environ.get('AWS_SECRET_NAME', 'ethiopian-business/prod')
_AWS_REGION   = os.environ.get('AWS_DEFAULT_REGION', 'af-south-1')

# Only these keys are mapped from Secrets Manager → os.environ
_ALLOWED_KEYS = frozenset({
    'DATABASE_URL',
    'FLASK_SECRET_KEY',
    'PROVIDER_ADMIN_PASSWORD',
    'DEFAULT_ADMIN_PASSWORD',
    'DEFAULT_HR_PASSWORD',
    'DEFAULT_ACCOUNTANT_PASSWORD',
    'DEFAULT_EMPLOYEE_PASSWORD',
    'DEFAULT_DATA_ENTRY_PASSWORD',
    'S3_BUCKET_BID_DOCS',
    'S3_BUCKET_BACKUPS',
})


def load_secrets() -> bool:
    """
    Fetch secrets from AWS Secrets Manager and populate os.environ.

    Returns True on success, False if Secrets Manager was not reachable
    (either not on AWS, no credentials, or secret not found).
    """
    try:
        import boto3
    except ImportError:
        logger.debug("boto3 not installed — skipping Secrets Manager (local dev mode)")
        return False

    try:
        client = boto3.client('secretsmanager', region_name=_AWS_REGION)
        response = client.get_secret_value(SecretId=_SECRET_NAME)
    except Exception as exc:
        # Covers: NoCredentialsError, EndpointResolutionError, ResourceNotFoundException, etc.
        logger.debug(
            "Secrets Manager not available (%s: %s) — using local env vars",
            type(exc).__name__, exc,
        )
        return False

    try:
        raw = response.get('SecretString') or ''
        secrets: dict = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.error("Failed to parse Secrets Manager payload: %s", exc)
        return False

    injected = []
    for key, value in secrets.items():
        # Only inject known keys; never override explicitly set env vars
        if key in _ALLOWED_KEYS and not os.environ.get(key):
            os.environ[key] = str(value)
            injected.append(key)

    if injected:
        logger.info(
            "Loaded %d secret(s) from AWS Secrets Manager [%s]: %s",
            len(injected), _SECRET_NAME, ', '.join(injected),
        )
    return True
