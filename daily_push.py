# daily_push.py - Render 的 Cron 入口，用來在每天 16:00 推播
from __future__ import annotations
import os
import re
from typing import Dict, List, Tuple

from linebot import LineBotApi
from linebot.models import TextSendMessage

from gcal_utils import get_tomorrow_events
from sheets_utils import get_patients  # 讀取 Google Sheet 的 displayName/realName/userId

TIMEZONE = os.getenv("TIMEZONE", "Asia/Taipei").strip()
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

# ---------- 名字抽取：支援「8外- 李家森 2」、「門診-王小明」等 ----------
# 取「最後一個 '-' 後面」到尾端，去掉尾端數字/空白
NAME_RE = re.compile(r".*-\s*([^\d\-\(\)（）]+?)(?:\s*\d+)?\s*$")

def extract_patient_name(summary: str) -> str:
    s = (summary or "").strip()
    m = NAME_RE.match(s)
    if m:
        return m.group(1).strip()
    # 後備：如果沒有 '-'，就拿整行去掉尾端數字
    s = re.sub(r"\s*\d+\s*$", "", s)
    return s.strip()


def tw_time_str(iso_str: str) -> str:
    """把 ISO 轉成 'MM 月 DD 日 HH:MM'（台灣常用）"""
    from datetime import datetime
    from dateutil import parser
    dt = parser.isoparse(iso_str)
    dt = dt.astimezone()  # 轉成本地時區
    return dt.strftime("%m 月 %d 日 %H:%M")


def build_patient_msg(start_iso: str) -> str:
    return f"（提醒您：您的治療預約在 {tw_time_str(start_iso)}，如需更改請提前告知喔！！）"


def group_events_for_me(events: List[Dict]) -> Tuple[str, List[Dict]]:
    """
    將我的行程整理成給管理者看的摘要（只含我自己），並回傳「找不到 userId」的清單方便補資料。
    """
    if not events:
        return "提醒（明天行程）\n明天沒有任何排程 ✅", []

    lines = ["提醒（明天行程）"]
    not_matched: List[Dict] = []
    for ev in events:
        title = ev.get("summary", "")
        loc = ev.get("location", "")
        s = tw_time_str(ev["start"])
        # 給管理者看的行數
        lines.append(f"．{s}　{title}" + (f"（{loc}）" if loc else ""))

    return "\n".join(lines), not_matched


def main():
    # 1) 讀日曆（只會拿「你自己的」行程；過濾在 gcal_utils 已經做了）
    events = get_tomorrow_events(TIMEZONE, CALENDAR_IDS, MY_EMAIL)

    # 2) 讀病人清單（Google Sheet）
    #    格式：displayName, realName, userId
    pats = get_patients()
    # 建兩個索引：displayName 與 realName 都能對到
    by_display: Dict[str, str] = { (p.get("displayName") or "").strip(): (p.get("userId") or "").strip() for p in pats if p.get("userId") }
    by_real: Dict[str, str] = { (p.get("realName") or "").strip(): (p.get("userId") or "").strip() for p in pats if p.get("userId") }

    # 3) 逐一行程 → 找病人 → 推播
    not_found: List[str] = []  # 找不到 userId 的名字，待你補資料
    for ev in events:
        name = extract_patient_name(ev.get("summary", ""))
        if not name:
            continue

        uid = by_real.get(name) or by_display.get(name)
        if uid:
            try:
                line_bot_api.push_message(uid, TextSendMessage(text=build_patient_msg(ev["start"])))
            except Exception as e:
                # 就算病人發送失敗，也不要影響後續流程；留待摘要告知你
                not_found.append(f"{name}（推播失敗：{e}）")
        else:
            not_found.append(name)

    # 4) 給管理者（只含你的行程清單；另外列出找不到 userId 的名單）
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