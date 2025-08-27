# gcal_utils.py — 只抓「你本人建立/參與」的明日行程（含台灣時區）
from __future__ import annotations

import os
import json
from typing import List, Dict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

CAL_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def _cal_service():
    """
    用環境變數 GOOGLE_SERVICE_ACCOUNT_JSON（Service Account JSON 內容本身）
    建立 Calendar API client。
    """
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError("缺少 GOOGLE_SERVICE_ACCOUNT_JSON（請貼入 Service Account JSON 內容）")

    try:
        info = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"GOOGLE_SERVICE_ACCOUNT_JSON 不是合法 JSON：{e}")

    creds = Credentials.from_service_account_info(info, scopes=CAL_SCOPES)
    return build("calendar", "v3", credentials=creds)


def _iso_with_tz(dt: datetime) -> str:
    """
    產生 RFC3339（含時區偏移）的字串。dt 必須為 tz-aware。
    例如 '2025-08-28T00:00:00+08:00'
    """
    if dt.tzinfo is None:
        raise ValueError("datetime 必須帶 tzinfo")
    # Calendar API 接受帶偏移的 ISO 8601
    return dt.isoformat()


def get_tomorrow_events(tz_name: str, calendar_ids: List[str], my_email: str) -> List[Dict]:
    """
    回傳只屬於『你本人』的明日行程清單：
    - 你是 creator / organizer，或
    - 你在 attendees 內且不是 declined
    每筆包含：start(ISO)、end(ISO)、summary、location
    """
    tz = ZoneInfo(tz_name)  # 例如 "Asia/Taipei"
    now = datetime.now(tz)

    # 明日 00:00 ~ 後日 00:00（含時區）
    start = datetime(now.year, now.month, now.day, tzinfo=tz) + timedelta(days=1)
    end = start + timedelta(days=1)

    time_min = _iso_with_tz(start)
    time_max = _iso_with_tz(end)

    service = _cal_service()
    results: List[Dict] = []

    for cal_id in calendar_ids:
        page_token = None
        while True:
            req = (
                service.events()
                .list(
                    calendarId=cal_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=2500,
                    timeZone=tz_name,  # 額外標明時區（保險）
                    pageToken=page_token,
                )
            )
            resp = req.execute()
            for ev in resp.get("items", []):
                # --- 僅保留「屬於你」的 ---
                creator_email = (ev.get("creator", {}) or {}).get("email", "")
                organizer_email = (ev.get("organizer", {}) or {}).get("email", "")

                is_mine = False
                if my_email and (
                    my_email.lower() == creator_email.lower()
                    or my_email.lower() == organizer_email.lower()
                ):
                    is_mine = True
                else:
                    for att in (ev.get("attendees") or []):
                        if att.get("email", "").lower() == my_email.lower() and att.get("responseStatus") != "declined":
                            is_mine = True
                            break

                if not is_mine:
                    continue

                # --- 取開始/結束時間（有些是整天事件） ---
                start_dt = (
                    ev.get("start", {}).get("dateTime")
                    or ev.get("start", {}).get("date")  # yyyy-mm-dd
                )
                end_dt = (
                    ev.get("end", {}).get("dateTime")
                    or ev.get("end", {}).get("date")
                )

                # date（整天）→ 補上 00:00 時區
                if start_dt and len(start_dt) == 10:
                    start_dt = f"{start_dt}T00:00:00{start.utcoffset().isoformat() if start.utcoffset() else '+00:00'}"
                if end_dt and len(end_dt) == 10:
                    end_dt = f"{end_dt}T00:00:00{end.utcoffset().isoformat() if end.utcoffset() else '+00:00'}"

                results.append(
                    {
                        "start": start_dt,
                        "end": end_dt,
                        "summary": ev.get("summary", ""),
                        "location": ev.get("location", ""),
                    }
                )

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    return results
