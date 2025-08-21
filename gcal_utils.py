# gcal_utils.py - Google Calendar helpers（使用環境變數存 JSON）
import os, json
from typing import List, Dict
from datetime import datetime, timedelta
import pytz
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

CAL_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

def _get_cal_service():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError("請在環境變數設定 GOOGLE_SERVICE_ACCOUNT_JSON（Service Account JSON 內容）。")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(raw.replace("'", '"'))
    creds = Credentials.from_service_account_info(data, scopes=CAL_SCOPES)
    return build("calendar", "v3", credentials=creds)

def _parse_dt(item, tz):
    start = item.get("start", {}) or {}
    if start.get("dateTime"):
        return datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00")).astimezone(tz)
    if start.get("date"):
        return tz.localize(datetime.strptime(start["date"] + "T00:00:00", "%Y-%m-%dT%H:%M:%S"))
    return tz.localize(datetime.now())

def get_tomorrow_events(timezone_name: str, calendar_ids: List[str], my_email: str) -> List[Dict]:
    tz = pytz.timezone(timezone_name or "Asia/Taipei")
    now = datetime.now(tz)
    start = tz.localize(datetime(now.year, now.month, now.day)) + timedelta(days=1)
    end = start + timedelta(days=1)

    service = _get_cal_service()
    all_events: List[Dict] = []
    for cid in calendar_ids:
        token = None
        while True:
            resp = service.events().list(
                calendarId=cid,
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                pageToken=token,
                maxResults=2500,
            ).execute()
            for it in resp.get("items", []):
                creator = it.get("creator") or {}
                creator_email = (creator.get("email") or "").lower()
                creator_self = bool(creator.get("self"))
                if my_email and creator_email != (my_email or "").lower() and not creator_self:
                    continue  # 僅取你建立的
                sdt = _parse_dt(it, tz)
                all_events.append({
                    "summary": it.get("summary", ""),
                    "start": sdt,
                    "location": it.get("location", ""),
                    "calendarId": cid,
                    "raw": it,
                })
            token = resp.get("nextPageToken")
            if not token:
                break
    all_events.sort(key=lambda e: e["start"])
    return all_events

def format_events_tw(events: List[Dict]) -> str:
    if not events:
        return "明天沒有任何排程 ✅"
    lines = ["📅 明天預約一覽："]
    for e in events:
        t = e["start"].strftime("%H:%M")
        title = e["summary"] or "(無標題)"
        loc = e.get("location") or ""
        lines.append(f"• {t}　{title}" + (f"（{loc}）" if loc else ""))
    return "\n".join(lines)