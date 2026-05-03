# monthly_stats.py
# 修改紀錄：
# 1. 支援查詢他人業績：新增 title_prefix 參數，可依據標題開頭 (例如 "8" 或 "8外") 來篩選行程。
# 2. 保留原有的 filter_by_me 邏輯，如果沒給代號，就用 Email 算自己的。

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

def fetch_events_in_range(start: datetime, end: datetime, filter_by_me: bool = True, title_prefix: str = None) -> List[Dict]:
    """
    如果 title_prefix 有值 (例如 "8")，就抓取標題開頭為該字串的行程 (不看 email)。
    否則，依照 filter_by_me 判斷是否只抓取屬於 MY_EMAIL 的行程。
    """
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
                
                summary = ev.get("summary", "") or ""

                # --- 篩選邏輯 ---
                if title_prefix:
                    # 模式 A：查特定代號 (只要標題開頭符合，例如 "8" 外，就抓進來)
                    if not summary.startswith(title_prefix):
                        continue
                elif filter_by_me:
                    # 模式 B：查自己 (用 Email 判斷)
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
                    "summary": summary,
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
def get_stats_report_text(target_date: datetime, title_prefix: str = None) -> str:
    """計算指定日期所在月份的完整業績，支援用標題前綴搜尋"""
    start, end = get_full_month_range(target_date)
    
    # 呼叫抓取函式
    events = fetch_events_in_range(start, end, filter_by_me=(not bool(title_prefix)), title_prefix=title_prefix)
    one_h, half_h, min45 = summarize_month(events)

    month_str = str(start.month)
    
    # 動態產生報表標題 (區分是你自己的還是某個代號的)
    header_name = f" [{title_prefix}]" if title_prefix else " (自己)"
    
    msg = (
        f"📊【{month_str}月 即時統計】{header_name}\n"
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
    
    if not is_second_to_last_day(now):
        print(f"[INFO] Today is {now.date()}, not the second to last day. Skip.")
        return

    print("[INFO] Target day matched! Pushing monthly stats...")
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
