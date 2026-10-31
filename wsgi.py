import os
import secrets
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

os.environ["GROWPROS_PUBLIC"] = "1"
secret_path = os.path.join(os.path.expanduser("~"), ".growpros_session_secret")
try:
    descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
        secret_file.write(secrets.token_hex(32))
except FileExistsError:
    pass

with open(secret_path, "r", encoding="utf-8") as secret_file:
    os.environ.setdefault("STUDYSYNC_SECRET_KEY", secret_file.read().strip())

from app import app as application
