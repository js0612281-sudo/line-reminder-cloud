# sheets_utils.py - Google Sheets storage for patients (displayName,realName,userId)
import os, json
from typing import List, Dict
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SHEET_ID = os.getenv("SHEET_ID", "").strip()
SHEET_NAME = os.getenv("SHEET_NAME", "Patients")
HEADER = ["displayName", "realName", "userId"]

def _creds():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError("缺少 GOOGLE_SERVICE_ACCOUNT_JSON（請把 service account JSON 內容整段貼到環境變數）")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(raw.replace("'", '"'))
    return Credentials.from_service_account_info(data, scopes=SCOPES)

def _sheet():
    if not SHEET_ID:
        raise RuntimeError("缺少 SHEET_ID（你的 Google 試算表 ID）")
    return build("sheets", "v4", credentials=_creds()).spreadsheets()

def _ensure_header():
    s = _sheet()
    resp = s.values().get(spreadsheetId=SHEET_ID, range=f"{SHEET_NAME}!A1:C1").execute()
    vals = resp.get("values", [])
    if not vals or vals[0] != HEADER:
        s.values().update(
            spreadsheetId=SHEET_ID,
            range=f"{SHEET_NAME}!A1:C1",
            valueInputOption="RAW",
            body={"values": [HEADER]},
        ).execute()

def read_patients() -> List[Dict[str, str]]:
    s = _sheet()
    _ensure_header()
    resp = s.values().get(spreadsheetId=SHEET_ID, range=f"{SHEET_NAME}!A2:C").execute()
    rows = resp.get("values", []) or []
    out: List[Dict[str, str]] = []
    for r in rows:
        dn = (r[0] if len(r) > 0 else "").strip()
        rn = (r[1] if len(r) > 1 else "").strip()
        uid = (r[2] if len(r) > 2 else "").strip()
        if uid:
            out.append({"displayName": dn, "realName": rn, "userId": uid})
    return out

def upsert_patient(display_name: str, user_id: str):
    """若 userId 已存在就更新 displayName；否則新增一列（realName 先留空）。"""
    s = _sheet()
    data = read_patients()
    # 找列號（資料從第 2 列開始）
    row_idx = None
    for i, r in enumerate(data, start=2):
        if r.get("userId") == user_id:
            row_idx = i
            break
    if row_idx is None:
        s.values().append(
            spreadsheetId=SHEET_ID,
            range=f"{SHEET_NAME}!A:C",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [[display_name, "", user_id]]},
        ).execute()
    else:
        s.values().update(
            spreadsheetId=SHEET_ID,
            range=f"{SHEET_NAME}!A{row_idx}:C{row_idx}",
            valueInputOption="RAW",
            body={"values": [[display_name, data[row_idx-2].get("realName", ""), user_id]]},
        ).execute()