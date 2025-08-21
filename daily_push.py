# daily_push.py - Render 的排程入口
import os
from typing import Dict, List, Tuple
import pytz
from linebot import LineBotApi
from linebot.models import TextSendMessage
from gcal_utils import get_tomorrow_events, format_events_tw
from sheets_utils import load_patients  # 目前用 CSV；之後可改 Google Sheet

TIMEZONE = os.getenv("TIMEZONE", "Asia/Taipei")
MY_EMAIL = os.getenv("MY_EMAIL", "")
CALENDAR_IDS = [c.strip() for c in os.getenv("CALENDAR_IDS", "primary").split(",") if c.strip()]
ADMIN_USER_IDS = [u.strip() for u in os.getenv("ADMIN_USER_IDS", "").split(",") if u.strip()]
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")

if not CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("缺少 CHANNEL_ACCESS_TOKEN")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)

def _patients_map() -> Dict[str, str]:
    rows = load_patients()
    mp = {}
    for r in rows:
        name = (r.get("realName") or r.get("displayName") or "").strip()
        uid = (r.get("userId") or "").strip()
        if name and uid:
            mp[name] = uid
    return mp

def _match_patient(summary: str, patients: Dict[str, str]) -> Tuple[str, str]:
    if not summary:
        return "", ""
    # 先比長的，避免「小明」誤配到「小明明」
    for name in sorted(patients.keys(), key=len, reverse=True):
        if name and name in summary:
            return name, patients[name]
    return "", ""

def _patient_msg(dt) -> str:
    return f"(提醒您：您的治療預約在 {dt.strftime('%m')} 月 {dt.strftime('%d')}日 {dt.strftime('%H:%M')}，如需更改請提前告知喔！！)"

def main():
    events = get_tomorrow_events(TIMEZONE, CALENDAR_IDS, MY_EMAIL)
    patients = _patients_map()

    sent_to: List[str] = []
    not_matched: List[str] = []

    for e in events:
        name, uid = _match_patient(e["summary"], patients)
        if uid:
            try:
                line_bot_api.push_message(uid, TextSendMessage(text=_patient_msg(e["start"])))
                sent_to.append(name)
            except Exception as ex:
                print(f"[PUSH FAIL] {name} -> {uid}: {ex}")
        else:
            not_matched.append(e["summary"])

    # 傳摘要給管理者
    if ADMIN_USER_IDS:
        summary = ["⏰ 提醒（明天行程）", format_events_tw(events), ""]
        if sent_to:
            summary.append("✅ 已推播給：\n- " + "\n- ".join(sent_to))
        if not_matched:
            summary.append("❓ 未對到名單：\n- " + "\n- ".join(not_matched))
        msg = "\n".join(summary)
        for admin in ADMIN_USER_IDS:
            try:
                line_bot_api.push_message(admin, TextSendMessage(text=msg))
            except Exception as ex:
                print(f"[ADMIN FAIL] -> {admin}: {ex}")

if __name__ == "__main__":
    main()