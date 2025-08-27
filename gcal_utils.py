# gcal_utils.py - Google Calendar helpers（使用 Service Account 讀取）
from __future__ import annotations
import os
import json
from typing import List, Dict
from datetime import datetime, timedelta, timezone

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

CAL_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def _cal_service():
    """
    建立 Calendar API service；使用 GOOGLE_SERVICE_ACCOUNT_JSON（Render 環境變數）
    """
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError("缺少 GOOGLE_SERVICE_ACCOUNT_JSON 環境變數")

    # 允許貼進來的是壓成一行的 JSON
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 有些平台會把換行吃掉，這裡再保險一次
        data = json.loads(raw.replace("\\n", "\n"))

    creds = Credentials.from_service_account_info(data, scopes=CAL_SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def get_tomorrow_events(tz: str, calendar_ids: List[str], my_email: str) -> List[Dict]:
    """
    只回傳「你自己的行程」：
    - 你是建立者（creator.email == my_email 或 creator.self == True）
    - 或你是 organizer
    - 或你在 attendees 之中
    產出欄位：start, end, summary, location
    """
    service = _cal_service()

    # 時區、時間窗（明天 00:00 ~ 後天 00:00）
    tzinfo = timezone(timedelta(hours=int(int(datetime.now().astimezone().utcoffset().total_seconds() // 3600))))
    try:
        # 以 IANA 時區為準（例如 Asia/Taipei）；如果傳進來了就用傳進來的
        tzinfo = datetime.now().astimezone().tzinfo if not tz else timezone(timedelta(hours=0))
    except Exception:
        pass

    now = datetime.now()
    # 用傳入 tz 來格式化邏輯（只影響輸出字串；API 仍用 ISO）
    start_dt = datetime(now.year, now.month, now.day) + timedelta(days=1)
    end_dt = start_dt + timedelta(days=1)

    time_min = start_dt.isoformat() + "Z" if "UTC" in tz.upper() else start_dt.isoformat()
    time_max = end_dt.isoformat() + "Z" if "UTC" in tz.upper() else end_dt.isoformat()

    results: List[Dict] = []

    for cal_id in calendar_ids:
        page_token = None
        while True:
            resp = (
                service.events()
                .list(
                    calendarId=cal_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=2500,
                )
                .execute()
            )

            for it in resp.get("items", []):
                if it.get("status") == "cancelled":
                    continue

                creator = it.get("creator", {}) or {}
                organizer = it.get("organizer", {}) or {}
                attendees = it.get("attendees", []) or []

                is_mine = False
                if creator.get("self") or (creator.get("email") or "").lower() == (my_email or "").lower():
                    is_mine = True
                if (organizer.get("email") or "").lower() == (my_email or "").lower():
                    is_mine = True
                if any((a.get("email") or "").lower() == (my_email or "").lower() for a in attendees):
                    is_mine = True

                if not is_mine:
                    continue

                # 整理時間
                s = it.get("start", {})
                e = it.get("end", {})
                if "dateTime" in s:
                    sdt = s["dateTime"]
                else:
                    sdt = (s.get("date") or "") + "T00:00:00"

                if "dateTime" in e:
                    edt = e["dateTime"]
                else:
                    edt = (e.get("date") or "") + "T00:00:00"

                results.append(
                    {
                        "start": sdt,
                        "end": edt,
                        "summary": it.get("summary", "") or "",
                        "location": it.get("location", "") or "",
                    }
                )

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    return results