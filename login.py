import os
import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import pyotp
from SmartApi import SmartConnect
from logzero import logger

key_path = "C:/angel" if os.path.exists("C:/angel/key.txt") else "."

def login():
    try:
        target_key = os.path.join(key_path, "key.txt")
        if not os.path.exists(target_key):
            target_key = "key.txt"

        with open(target_key, "r") as f:
            keys = f.read().split()
        api_key = keys[0]
        username = keys[2]
        password = keys[3]
        totp = pyotp.TOTP(keys[4]).now()

        smartApi = SmartConnect(api_key)
        session = smartApi.generateSession(username, password, totp)

        if session.get("status") is True:
            print("✅ Login successful")
            logger.info("SmartAPI login successful.")
            return smartApi
        else:
            print("❌ Login failed:", session.get("message", "No message"))
            logger.error(f"Login failed: {session}")
            exit()
    except Exception as e:
        logger.error(f"Login error: {e}")
        print("❌ Login script error:", e)
        exit()

if __name__ == "__main__":
    api = login()
    print("🎯 Login object initialized:", api)