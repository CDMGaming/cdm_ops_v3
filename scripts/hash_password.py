"""
Generate the bcrypt hash to put in .env as APP_PASSWORD_HASH.

Run inside the app container (it already has passlib installed):
  docker exec -it cdm-ops python scripts/hash_password.py
"""
import getpass
from passlib.hash import bcrypt

pw = getpass.getpass("New CDM Ops login password: ")
confirm = getpass.getpass("Confirm: ")
if pw != confirm:
    raise SystemExit("Passwords didn't match.")
print("\nPaste this into .env as APP_PASSWORD_HASH, then restart the app container:\n")
print(bcrypt.hash(pw))
