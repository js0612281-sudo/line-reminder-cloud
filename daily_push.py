# daily_push.py
# Render Cron 入口：抓明日多日曆行程 → 推送摘要給管理者
import os
from typing import List

# 仍使用 v2 的 line-bot-sdk；會看到 Deprecated 警告，但可正常運作
from linebot import LineBotApi
from linebot.models import TextSendMessage

from gcal_utils import get_tomorrow_events, format_events_tw, debug_list_calendars

# ====== 環境變數 ======
TIMEZONE = os.getenv("TIMEZONE", "Asia/Taipei").strip()

def _parse_csv_env(key: str) -> List[str]:
    """把以半形逗號分隔的環境變數轉成乾淨的 list。"""
    raw = os.getenv(key, "").strip()
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]

CALENDAR_IDS = _parse_csv_env("CALENDAR_IDS")
MY_EMAIL = os.getenv("MY_EMAIL", "").strip()

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN", "").strip()
if not CHANNEL_ACCESS_TOKEN:
    raise ValueError("環境變數 CHANNEL_ACCESS_TOKEN 未設定")

ADMIN_USER_IDS = _parse_csv_env("ADMIN_USER_IDS")
if not ADMIN_USER_IDS:
    print("[WARN] ADMIN_USER_IDS 為空，將不會有人收到摘要訊息。")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)


def main():
    # --- 除錯：列出目前 service account 與它看得到的日曆 ---
    visible = debug_list_calendars()
    print(f"[DEBUG] service account can see {len(visible)} calendars")

    # --- 真正抓資料 ---
    print(f"[DEBUG] CALENDAR_IDS = {CALENDAR_IDS}")
    events = get_tomorrow_events(TIMEZONE, CALENDAR_IDS, MY_EMAIL)
    print(f"[DEBUG] fetched events count = {len(events)}")

    # --- 管理者摘要 ---
    summary = format_events_tw(events)

    if not ADMIN_USER_IDS:
        print("[DEBUG] 沒有 ADMIN_USER_IDS，僅印出摘要：\n" + summary)
        return

    for uid in ADMIN_USER_IDS:
        try:
            line_bot_api.push_message(uid, TextSendMessage(text=summary))
            print(f"[PUSH OK] -> {uid}")
        except Exception as e:
            print(f"[PUSH FAIL] -> {uid}: {e}")


if __name__ == "__main__":
    main()
