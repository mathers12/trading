"""Posielanie upozornení na Telegram (alebo výpis do konzoly, ak Telegram nie je nastavený)."""
from __future__ import annotations

import os

import requests


def send(text: str) -> bool:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[telegram nie je nastavený]\n" + text + "\n")
        return False
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=20)
    if not r.ok:
        print(f"Telegram chyba {r.status_code}: {r.text[:200]}")
    return r.ok
