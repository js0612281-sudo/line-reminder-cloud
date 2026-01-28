# daily_push.py - Render 的 Cron 入口，用來在每天 16:00 推播
# 修改紀錄：更新 extract_patient_name 邏輯，支援忽略行事曆標題後的括號與備註 (e.g. "8外-王小明 2 (1F)")

from __future__ import annotations
import os
import re
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo

from linebot import LineBotApi
from linebot.models import TextSendMessage

from gcal_utils import get_tomorrow_events
from sheets_utils import read_patients  # 讀取 Google Sheet 的 displayName/realName/userId

# ---- 基本設定 ----
TIMEZONE = os.getenv("TIMEZONE", "Asia/Taipei").strip()
TZ = ZoneInfo(TIMEZONE)
MY_EMAIL = os.getenv("MY_EMAIL", "").strip()

# 多個日曆用逗號
raw_cals = os.getenv("CALENDAR_IDS", "").strip()
if not raw_cals:
    raise RuntimeError("請在環境變數 CALENDAR_IDS 設定你的日曆 ID（可逗號分隔）")
CALENDAR_IDS: List[str] = [c.strip() for c in raw_cals.split(",") if c.strip()]

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN", "").strip()
if not CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("缺少 CHANNEL_ACCESS_TOKEN")

ADMIN_USER_IDS = [u.strip() for u in os.getenv("ADMIN_USER_IDS", "").split(",") if u.strip()]
if not ADMIN_USER_IDS:
    raise RuntimeError("缺少 ADMIN_USER_IDS（至少含你自己的 userId）")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)

# ---------- (舊的 NAME_RE 移除，改用新邏輯) ----------

def extract_patient_name(summary: str) -> str:
    """
    從行事曆標題中抓取病人姓名。
    邏輯：
    1. 找到 '-' 號，取後面的字串。
    2. 往後讀取，一旦遇到「數字」或「左括號 ( / （」，就切斷。
    3. 去除前後空白即為名字。
    
    範例：
    "8外- 張駿之 2 (1F)" -> "張駿之"
    "門診-王小明(新患)" -> "王小明"
    "8外- 李家森"       -> "李家森"
    """
    s = (summary or "").strip()
    
    # 1. 如果有 '-'，只取減號後面的部分 (e.g. "8外- 張駿之..." -> " 張駿之...")
    if "-" in s:
        # split 參數 1 代表只切第一刀，避免名字裡剛好也有減號(雖然少見)
        s = s.split("-", 1)[1]
    
    # 2. 去除開頭空白
    s = s.strip()

    # 3. 用 Regex 找「第一個」干擾符號的位置 (數字、半形左括號、全形左括號)
    # 只要遇到這些符號，就代表名字結束了
    match = re.search(r"[\d\(\（]", s)
    if match:
        # 在干擾符號的位置切斷
        s = s[:match.start()]
    
    return s.strip()


def tw_time_str(iso_str: str) -> str:
    """
    把 ISO 轉成 'MM 月 DD 日 HH:MM'（固定轉成 TIMEZONE，例如 Asia/Taipei）。
    若是整天事件（ISO 裡沒有時間），會回傳 'MM 月 DD 日 整天'
    """
    from dateutil import parser
    # 判斷是否為「只有日期」的整天事件
    if len(iso_str) == 10 and iso_str.count("-") == 2:
        # yyyy-mm-dd
        dt = parser.isoparse(iso_str).replace(tzinfo=TZ)
        return dt.strftime("%m 月 %d 日 整天")
    # 一般有時間的事件
    dt = parser.isoparse(iso_str)
    if dt.tzinfo is None:
        # 沒帶時區就視為目標時區
        dt = dt.replace(tzinfo=TZ)
    # 明確轉成台北時區顯示
    dt = dt.astimezone(TZ)
    return dt.strftime("%m 月 %d 日 %H:%M")


def build_patient_msg(start_iso: str) -> str:
    when = tw_time_str(start_iso)
    return f"提醒您：您的治療預約在 {when}，如需更改請提前告知喔！！"


def group_events_for_me(events: List[Dict]) -> Tuple[str, List[Dict]]:
    """將我的行程整理成給管理者看的摘要（只含我自己），並回傳找不到 userId 的清單。"""
    if not events:
        return "提醒（明天行程）\n明天沒有任何排程 ✅", []

    lines = ["提醒（明天行程）"]
    not_matched: List[Dict] = []
    for ev in events:
        title = ev.get("summary", "")
        loc = ev.get("location", "")
        s = tw_time_str(ev["start"])
        
        # 在摘要裡顯示原始標題，方便核對
        lines.append(f"．{s}　{title}" + (f"（{loc}）" if loc else ""))

    return "\n".join(lines), not_matched


def main():
    # 1) 只拿「你的」行程（gcal_utils 已過濾 creator/organizer/attendee 為你）
    events = get_tomorrow_events(TIMEZONE, CALENDAR_IDS, MY_EMAIL)

    # 2) 讀病人清單（Google Sheet：displayName, realName, userId）
    pats = read_patients()
    by_display: Dict[str, str] = { (p.get("displayName") or "").strip(): (p.get("userId") or "").strip() for p in pats if p.get("userId") }
    by_real:   Dict[str, str] = { (p.get("realName")   or "").strip(): (p.get("userId") or "").strip() for p in pats if p.get("userId") }

    # 3) 逐一行程 → 找病人 → 推播
    not_found: List[str] = []
    for ev in events:
        # 使用新的抓名邏輯
        summary = ev.get("summary", "")
        
        # 這裡加個保險：如果標題根本沒有 '-'，通常不是掛號，直接跳過 (避免抓到 "午休" 這種)
        if "-" not in summary:
            continue
            
        name = extract_patient_name(summary)
        if not name:
            continue

        uid = by_real.get(name) or by_display.get(name)
        if uid:
            try:
                line_bot_api.push_message(uid, TextSendMessage(text=build_patient_msg(ev["start"])))
            except Exception as e:
                not_found.append(f"{name}（推播失敗：{e}）")
        else:
            not_found.append(name)

    # 4) 管理者摘要（只含你的行程）＋ 待補名單
    admin_text, _ = group_events_for_me(events)
    if not_found:
        admin_text += "\n\n【待補名單】\n- " + "\n- ".join(sorted(set(not_found)))

    for uid in ADMIN_USER_IDS:
        try:
            line_bot_api.push_message(uid, TextSendMessage(text=admin_text))
        except Exception:
            pass


if __name__ == "__main__":
    main()
