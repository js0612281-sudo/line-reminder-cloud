# gcal_utils.py
# Google Calendar helpers：讀 Service Account、列出可見日曆、抓明日事件、格式化摘要
import os
import json
from datetime import datetime, timedelta
from typing import List, Dict

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

CAL_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def _build_creds() -> Credentials:
    """從環境變數 GOOGLE_SERVICE_ACCOUNT_JSON 建立憑證，並印出目前使用的 service account。"""
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON 未設定")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"GOOGLE_SERVICE_ACCOUNT_JSON 不是有效 JSON：{e}")

    client_email = data.get("client_email")
    print(f"[DEBUG] Using service account: {client_email}")

    creds = Credentials.from_service_account_info(data, scopes=CAL_SCOPES)
    return creds


def get_cal_service():
    """回傳 Google Calendar API service。"""
    creds = _build_creds()
    # 停用 discovery cache，避免無寫入權限環境出錯
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def debug_list_calendars() -> List[str]:
    """
    列出目前這個 Service Account 看得到的所有 calendarId。
    會在 log 印出：
      [DEBUG] visible calendar: <calendarId>  (<summary>)
    """
    svc = get_cal_service()
    ids: List[str] = []
    token = None
    while True:
        resp = svc.calendarList().list(pageToken=token, maxResults=250).execute()
        for item in resp.get("items", []):
            cid = item.get("id")
            summary = item.get("summary", "")
            print(f"[DEBUG] visible calendar: {cid}  ({summary})")
            ids.append(cid)
        token = resp.get("nextPageToken")
        if not token:
            break
    return ids


def get_tomorrow_events(timezone: str, calendar_ids: List[str], my_email: str) -> List[Dict]:
    """
    取『明天』的所有事件（多日曆彙整）。
    timezone 目前僅作語意標示；查詢時間帶以 +08:00 為例，可依需要調整。
    """
    svc = get_cal_service()

    now = datetime.now()
    start = datetime(now.year, now.month, now.day) + timedelta(days=1)
    end = start + timedelta(days=1)

    # 也可以改為依 timezone 轉換，這裡為簡潔直接固定 +08:00
    time_min = start.isoformat() + "+08:00"
    time_max = end.isoformat() + "+08:00"

    all_events: List[Dict] = []
    for cid in calendar_ids:
        print(f"[DEBUG] querying calendar: {cid}")
        page_token = None
        while True:
            resp = (
                svc.events()
                .list(
                    calendarId=cid,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=2500,
                    pageToken=page_token,
                )
                .execute()
            )
            all_events.extend(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    return all_events


def format_events_tw(events: List[Dict]) -> str:
    """將事件整理成中文清單文字（管理者摘要用）。"""
    if not events:
        return "提醒（明天行程）：\n明天沒有任何排程 ✅"

    lines = ["提醒（明天行程）："]
    for ev in events:
        st = ev.get("start") or {}
        ed = ev.get("end") or {}
        st_raw = st.get("dateTime") or (st.get("date") + "T00:00:00+08:00")
        ed_raw = ed.get("dateTime") or (ed.get("date") + "T23:59:00+08:00")

        try:
            st_dt = datetime.fromisoformat(st_raw.replace("Z", "+00:00"))
            ed_dt = datetime.fromisoformat(ed_raw.replace("Z", "+00:00"))
            t_str = f"{st_dt.strftime('%H:%M')}–{ed_dt.strftime('%H:%M')}"
        except Exception:
            t_str = "整天"

        title = (ev.get("summary") or "").strip() or "（無標題）"
        loc = (ev.get("location") or "").strip()
        tail = f"（{loc}）" if loc else ""
        lines.append(f"・{t_str}  {title}{tail}")

    return "\n".join(lines)
