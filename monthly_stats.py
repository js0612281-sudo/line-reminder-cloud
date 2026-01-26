# monthly_stats.py
# 每天跑一次，只有在「每月 1 號」時，才彙整「上個月」的人次並推送給管理者
# 修改紀錄：加入過濾機制，只統計包含 "-" 的行程，避免誤算私人行程。

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

# ======== 工具：日期區間（上個月）========
def get_last_month_range(now: datetime) -> Tuple[datetime, datetime]:
    """
    回傳『上個月』的 [月初00:00, 下月初00:00) 的時段（含時區）。
    邏輯：若今天是 5/1，本月月初是 5/1，上個月結束就是 5/1，上個月開始是 4/1。
    """
    # 取得本月 1 號 (00:00:00)
    this_month_first = datetime(now.year, now.month, 1, tzinfo=TZ)
    
    # 往前推一天到「上個月」，再把日子設為 1 號，即得「上個月月初」
    # 例如：5/1 - 1 day = 4/30 -> replace day=1 -> 4/1
    last_month_any_day = this_month_first - timedelta(days=1)
    last_month_first = last_month_any_day.replace(day=1)
    
    # 區間為 [上月1號, 本月1號)
    return last_month_first, this_month_first

# ======== 抓取日曆事件（只保留「你的」事件）========
def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("datetime must be tz-aware")
    return dt.isoformat()

def fetch_my_events_in_range(start: datetime, end: datetime) -> List[Dict]:
    """
    取出在 [start, end) 期間、所有 CALENDAR_IDS 中「屬於你」的事件。
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

# ======== 解析：人次加總邏輯 ========
RE_45 = re.compile(r"(45\s*(?:min|分鐘|分)?)", re.IGNORECASE)
RE_MULTI = re.compile(r"(\d)(?:\s*\+\s*(\d))+")

def count_session_from_title(title: str) -> Tuple[int, int, int]:
    t = (title or "").strip()
    if RE_45.search(t):
        return (0, 0, 1) # 45min
    
    m_multi = RE_MULTI.search(t)
    if m_multi:
        hours = 0
        halves = 0
        nums = [int(x) for x in re.findall(r"\d", t[m_multi.start():])]
        for n in nums:
            if n == 2: hours += 1
            elif n == 1: halves += 1
        return (hours, halves, 0)

    m_end = re.search(r"(\d)\s*$", t)
    if m_end:
        n = int(m_end.group(1))
        if n == 2: return (1, 0, 0)
        elif n == 1: return (0, 1, 0)
    
    return (0, 1, 0) # 預設半小時

def summarize_month(events: List[Dict]) -> Tuple[int, int, int]:
    one_h = half_h = min45 = 0
    for ev in events:
        title = ev.get("summary", "")
        
        # --- 新增：關鍵過濾器 ---
        # 如果標題裡面沒有「-」，就認定它是私人行程或雜事，直接跳過不統計
        if "-" not in title:
            continue
        # ---------------------

        a, b, c = count_session_from_title(title)
        one_h += a
        half_h += b
        min45 += c
    return one_h, half_h, min45

# ======== 主流程 ========
def main():
    now = datetime.now(TZ)
    
    # 關鍵：只在「每月 1 號」執行，否則直接結束
    if now.day != 1:
        print(f"[INFO] Today is {now.day}, not the 1st day of month. Skip stats.")
        return

    print("[INFO] Today is the 1st day! Generating last month's stats...")

    # 計算「上個月」的區間
    start, end = get_last_month_range(now)
    
    # 抓取並統計
    events = fetch_my_events_in_range(start, end)
    one_h, half_h, min45 = summarize_month(events)

    # 顯示月份 (抓 start 的月份即為上個月)
    month_str = str(start.month)

    msg = (
        f"📊【{month_str}月 統計報告】\n"
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
