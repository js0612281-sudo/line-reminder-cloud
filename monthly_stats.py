# monthly_stats.py
# 每天跑一次，但只有在「本月最後一天」時，才彙整當月人次並推送給管理者
from __future__ import annotations
import os
import re
import sys
from typing import List, Dict, Tuple
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# LINE
from linebot import LineBotApi
from linebot.models import TextSendMessage

# Google Calendar
import json
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ======== 環境變數 ========
TIMEZONE = os.getenv("TIMEZONE", "Asia/Taipei").strip()
TZ = ZoneInfo(TIMEZONE)

MY_EMAIL = os.getenv("MY_EMAIL", "").strip()

def _parse_csv_env(key: str) -> List[str]:
    raw = os.getenv(key, "") or ""
    return [x.strip() for x in raw.split(",") if x.strip()]

CALENDAR_IDS = _parse_csv_env("CALENDAR_IDS")  # 多顆日曆逗號分隔
ADMIN_USER_IDS = _parse_csv_env("ADMIN_USER_IDS")

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN", "").strip()
if not CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("缺少 CHANNEL_ACCESS_TOKEN")

# ======== LINE ========
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)

# ======== Google Calendar Client（使用 Service Account）========
CAL_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

def _cal_service():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError("缺少 GOOGLE_SERVICE_ACCOUNT_JSON")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        info = json.loads(raw.replace("\\n", "\n"))  # 有些平台會轉義換行
    creds = Credentials.from_service_account_info(info, scopes=CAL_SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)

# ======== 工具：日期區間（整個月份）========
def month_range(dt: datetime) -> Tuple[datetime, datetime]:
    """回傳當月 [月初00:00, 下月初00:00) 的時段（含時區）"""
    first = datetime(dt.year, dt.month, 1, tzinfo=TZ)
    if dt.month == 12:
        next_first = datetime(dt.year + 1, 1, 1, tzinfo=TZ)
    else:
        next_first = datetime(dt.year, dt.month + 1, 1, tzinfo=TZ)
    return first, next_first

def is_last_day_of_month(dt: datetime) -> bool:
    """用『明天是否跨月』判斷今天是否為月底"""
    tomorrow = (dt + timedelta(days=1)).date()
    return tomorrow.month != dt.month

# ======== 抓取日曆事件（只保留「你的」事件）========
def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("datetime must be tz-aware")
    return dt.isoformat()

def fetch_my_events_in_range(start: datetime, end: datetime) -> List[Dict]:
    """
    取出在 [start, end) 期間、所有 CALENDAR_IDS 中「屬於你」的事件。
    判斷邏輯：
      - 你是 creator / organizer，或
      - 你在 attendees 內且不是 declined
    回傳部分欄位：summary, start(date/dateTime), end(date/dateTime), location
    """
    svc = _cal_service()
    results: List[Dict] = []
    time_min = _iso(start)
    time_max = _iso(end)

    for cal_id in CALENDAR_IDS:
        page_token = None
        while True:
            resp = svc.events().list(
                calendarId=cal_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=2500,
                timeZone=TIMEZONE,
                pageToken=page_token,
            ).execute()

            for ev in resp.get("items", []):
                if ev.get("status") == "cancelled":
                    continue

                creator_email = (ev.get("creator") or {}).get("email", "")
                organizer_email = (ev.get("organizer") or {}).get("email", "")
                attendees = ev.get("attendees") or []

                me = (MY_EMAIL or "").lower()
                is_mine = False
                if me:
                    if creator_email.lower() == me or organizer_email.lower() == me:
                        is_mine = True
                    elif any((a.get("email") or "").lower() == me and a.get("responseStatus") != "declined"
                             for a in attendees):
                        is_mine = True

                if not is_mine:
                    continue

                results.append({
                    "summary": ev.get("summary", "") or "",
                    "start": ev.get("start", {}),
                    "end": ev.get("end", {}),
                    "location": ev.get("location", "") or "",
                })

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    return results

# ======== 解析：把事件標題轉為「一小時/半小時/45 分鐘」的人次加總 ========
# 規則：
# - 結尾 "… 2" 代表 1 小時
# - 沒有結尾數字 → 半小時
# - "2+2" 表示兩個 1 小時 → 一小時 +2
# - "2+1" 表示 1.5 小時 → 一小時 +1、半小時 +1
# - 含 "45" 或 "45min/45 分/45分鐘" → 視為 45 分鐘（優先）
RE_45 = re.compile(r"(45\s*(?:min|分鐘|分)?)", re.IGNORECASE)
RE_BLOCKS = re.compile(r"(?:(\d)\s*(?:\+\s*\d)*)$")   # 用於偵測結尾是否有數字
RE_MULTI = re.compile(r"(\d)(?:\s*\+\s*(\d))+")

def count_session_from_title(title: str) -> Tuple[int, int, int]:
    """
    傳回 (一小時人次, 半小時人次, 45分鐘人次) 的增量
    """
    t = (title or "").strip()

    # 45 分鐘優先處理
    if RE_45.search(t):
        return (0, 0, 1)

    # 2+2 / 2+1 這種
    m_multi = RE_MULTI.search(t)
    if m_multi:
        hours = 0
        halves = 0
        # 把所有數字都抓出來（例如 '2+1+2'）
        nums = [int(x) for x in re.findall(r"\d", t[m_multi.start():])]
        for n in nums:
            if n == 2:
                hours += 1
            elif n == 1:
                halves += 1
        return (hours, halves, 0)

    # 單一結尾數字
    m_end = re.search(r"(\d)\s*$", t)
    if m_end:
        n = int(m_end.group(1))
        if n == 2:
            return (1, 0, 0)  # 一小時 +1
        elif n == 1:
            return (0, 1, 0)  # 半小時 +1

    # 沒有任何數字 → 預設半小時
    return (0, 1, 0)

def summarize_month(events: List[Dict]) -> Tuple[int, int, int]:
    one_h = half_h = min45 = 0
    for ev in events:
        title = ev.get("summary", "")
        a, b, c = count_session_from_title(title)
        one_h += a
        half_h += b
        min45 += c
    return one_h, half_h, min45

# ======== 主流程 ========
def main():
    now = datetime.now(TZ)
    # 非月底就直接結束（不回任何訊息）
    if not is_last_day_of_month(now):
        print("[INFO] Not the last day of month; skip sending.")
        return

    start, end = month_range(now)
    events = fetch_my_events_in_range(start, end)
    one_h, half_h, min45 = summarize_month(events)

    month_str = now.strftime("%-m") if hasattr(now, "strftime") else str(now.month)  # Linux 可用 %-m
    # Windows 的 strftime 不支援 %-m，但 Render 是 Linux；備用方案：
    month_str = str(now.month)

    msg = (
        f"{month_str}月的總人次\n"
        f"一小時：{one_h}\n"
        f"半小時：{half_h}\n"
        f"45分鐘：{min45}"
    )

    if not ADMIN_USER_IDS:
        print("[WARN] ADMIN_USER_IDS 空白，無人可收統計。統計內容如下：\n" + msg)
        return

    for uid in ADMIN_USER_IDS:
        try:
            line_bot_api.push_message(uid, TextSendMessage(text=msg))
            print(f"[PUSH OK] -> {uid}")
        except Exception as e:
            print(f"[PUSH FAIL] -> {uid}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
