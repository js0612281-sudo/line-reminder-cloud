# monthly_stats.py
# 修改紀錄：
# 1. 將「產生報表文字」的邏輯拆解成 get_stats_report_text()，方便 app.py 隨時呼叫。
# 2. main() 仍保留「倒數第二天」自動推播的功能。

from __future__ import annotations
import os
import re
import sys
import calendar
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

CALENDAR_IDS = _parse_csv_env("CALENDAR_IDS")
ADMIN_USER_IDS = _parse_csv_env("ADMIN_USER_IDS")

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN", "").strip()
if not CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("缺少 CHANNEL_ACCESS_TOKEN")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
CAL_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

def _cal_service():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError("缺少 GOOGLE_SERVICE_ACCOUNT_JSON")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        info = json.loads(raw.replace("\\n", "\n"))
    creds = Credentials.from_service_account_info(info, scopes=CAL_SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)

# ======== 日期計算邏輯 ========

def get_full_month_range(now: datetime) -> Tuple[datetime, datetime]:
    """回傳『當月 1 號 00:00』到『下個月 1 號 00:00』(完整包含本月)"""
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    last_day_date = now.replace(day=days_in_month)
    end = last_day_date + timedelta(days=1)
    end = end.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, end

def is_second_to_last_day(now: datetime) -> bool:
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    target_day = days_in_month - 1
    return now.day == target_day

# ======== 抓取與統計 ========

def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("datetime must be tz-aware")
    return dt.isoformat()

def fetch_my_events_in_range(start: datetime, end: datetime) -> List[Dict]:
    svc = _cal_service()
    results: List[Dict] = []
    time_min = _iso(start)
    time_max = _iso(end)

    for cal_id in CALENDAR_IDS:
        page_token = None
        while True:
            resp = svc.events().list(
                calendarId=cal_id, timeMin=time_min, timeMax=time_max,
                singleEvents=True, orderBy="startTime", maxResults=2500,
                timeZone=TIMEZONE, pageToken=page_token,
            ).execute()

            for ev in resp.get("items", []):
                if ev.get("status") == "cancelled": continue
                
                creator = (ev.get("creator") or {}).get("email", "").lower()
                organizer = (ev.get("organizer") or {}).get("email", "").lower()
                attendees = ev.get("attendees") or []
                me = (MY_EMAIL or "").lower()
                
                is_mine = False
                if me:
                    if creator == me or organizer == me: is_mine = True
                    elif any((a.get("email") or "").lower() == me and a.get("responseStatus") != "declined" for a in attendees):
                        is_mine = True
                if not is_mine: continue
                
                results.append({
                    "summary": ev.get("summary", "") or "",
                    "start": ev.get("start", {}),
                    "end": ev.get("end", {}),
                    "location": ev.get("location", "") or "",
                })

            page_token = resp.get("nextPageToken")
            if not page_token: break
    return results

RE_45 = re.compile(r"(45\s*(?:min|分鐘|分)?)", re.IGNORECASE)
RE_MULTI = re.compile(r"(\d)(?:\s*\+\s*(\d))+")

def count_session_from_title(title: str) -> Tuple[int, int, int]:
    t = (title or "").strip()
    if RE_45.search(t): return (0, 0, 1)
    
    m_multi = RE_MULTI.search(t)
    if m_multi:
        hours = halves = 0
        nums = [int(x) for x in re.findall(r"\d", t[m_multi.start():])]
        for n in nums:
            if n == 2: hours += 1
            elif n == 1: halves += 1
        return (hours, halves, 0)

    m_end = re.search(r"(\d)\s*(?:\(.*\)|（.*）)?\s*$", t)
    if m_end:
        n = int(m_end.group(1))
        if n == 2: return (1, 0, 0)
        elif n == 1: return (0, 1, 0)
    
    return (0, 1, 0)

def summarize_month(events: List[Dict]) -> Tuple[int, int, int]:
    one_h = half_h = min45 = 0
    for ev in events:
        title = ev.get("summary", "")
        if "-" not in title: continue
        a, b, c = count_session_from_title(title)
        one_h += a; half_h += b; min45 += c
    return one_h, half_h, min45

# ======== 新增：產生報表文字的核心函式 (給 app.py 呼叫用) ========
def get_stats_report_text(target_date: datetime) -> str:
    """計算指定日期所在月份的完整業績，回傳文字報表"""
    # 計算範圍：月初 ~ 下月初 (完整包含本月)
    start, end = get_full_month_range(target_date)
    
    events = fetch_my_events_in_range(start, end)
    one_h, half_h, min45 = summarize_month(events)

    month_str = str(start.month)
    msg = (
        f"📊【{month_str}月 即時統計】\n"
        f"(含本月所有已安排行程)\n"
        f"------------------\n"
        f"一小時：{one_h}\n"
        f"半小時：{half_h}\n"
        f"45分鐘：{min45}"
    )
    return msg

# ======== 主流程 (Cron Job 呼叫用) ========
def main():
    now = datetime.now(TZ)
    
    # 1. 自動檢查：如果不是倒數第二天，就安靜結束
    if not is_second_to_last_day(now):
        print(f"[INFO] Today is {now.date()}, not the second to last day. Skip.")
        return

    print("[INFO] Target day matched! Pushing monthly stats...")
    
    # 2. 呼叫核心函式取得報表
    msg = get_stats_report_text(now)

    if not ADMIN_USER_IDS:
        return

    for uid in ADMIN_USER_IDS:
        try:
            line_bot_api.push_message(uid, TextSendMessage(text=msg))
        except Exception:
            pass

if __name__ == "__main__":
    main()
