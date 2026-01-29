# app.py - Flask LINE webhook (Render) + Task Triggers
# 修改紀錄：新增「查業績」指令，手動觸發月報表回覆

import os
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, request, abort, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent

# 匯入工具
from sheets_utils import upsert_patient
import daily_push
import monthly_stats  # 匯入剛改好的統計模組

# --- 必填環境變數 ---
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET", "").strip()
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN", "").strip()
CRON_SECRET = os.getenv("CRON_SECRET", "my-secret-key") 

if not CHANNEL_SECRET or not CHANNEL_ACCESS_TOKEN:
    raise ValueError("缺少 CHANNEL_SECRET 或 CHANNEL_ACCESS_TOKEN")

# 管理者設定
DEV_ONLY_PREFIX = os.getenv("DEV_ONLY_PREFIX", "#dev")
ADMIN_USER_IDS = {u.strip() for u in os.getenv("ADMIN_USER_IDS", "").split(",") if u.strip()}

# 時區設定 (給手動查詢用)
TIMEZONE = os.getenv("TIMEZONE", "Asia/Taipei").strip()
TZ = ZoneInfo(TIMEZONE)

app = Flask(__name__)
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

@app.get("/")
def health():
    return "OK (System is running)"

# --- LINE Webhook ---
@app.post("/callback")
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

# --- 排程觸發入口 (給外部 Cron 服務呼叫) ---

@app.get("/tasks/daily-push")
def trigger_daily_push():
    auth_header = request.headers.get("X-Cron-Secret")
    auth_query = request.args.get("key")
    if (auth_header != CRON_SECRET) and (auth_query != CRON_SECRET):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        daily_push.main() 
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.get("/tasks/monthly-stats")
def trigger_monthly_stats():
    auth_header = request.headers.get("X-Cron-Secret")
    auth_query = request.args.get("key")
    if (auth_header != CRON_SECRET) and (auth_query != CRON_SECRET):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        monthly_stats.main()
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# --- LINE 事件處理 ---

@handler.add(FollowEvent)
def on_follow(event: FollowEvent):
    uid = event.source.user_id
    display_name = ""
    try:
        prof = line_bot_api.get_profile(uid)
        display_name = prof.display_name or ""
    except Exception:
        pass
    try:
        upsert_patient(display_name, uid)
        print(f"[SHEET UPSERT SUCCESS] {display_name} ({uid})")
    except Exception as e:
        print(f"[SHEET UPSERT FAIL] {uid}: {e}")

@handler.add(MessageEvent, message=TextMessage)
def on_message(event: MessageEvent):
    uid = event.source.user_id
    text = (event.message.text or "").strip()

    # 1. 自動補登資料 (針對舊病人)
    try:
        profile = line_bot_api.get_profile(uid)
        display_name = profile.display_name or "Unknown"
        upsert_patient(display_name, uid)
    except Exception:
        pass

    # 2. 權限檢查
    is_admin = uid in ADMIN_USER_IDS
    
    # --- 新增功能：手動查業績 ---
    # 只有管理員可以查，關鍵字：「查業績」或「業績」
    if is_admin and text in ["查業績", "業績", "查詢業績"]:
        try:
            # 傳送「計算中」的提示，避免等太久以為當機
            # (但 LINE 回覆 token 只能用一次，所以我們直接讓它等一下下算出結果再回比較簡單)
            
            # 呼叫 monthly_stats 算立即的報表
            report_msg = monthly_stats.get_stats_report_text(datetime.now(TZ))
            
            # 回覆訊息
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=report_msg))
            return # 處理完就結束，不往下執行
        except Exception as e:
            error_msg = f"查詢失敗：{str(e)}"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=error_msg))
            return

    # 3. 原本的測試指令 (#dev)
    is_dev = DEV_ONLY_PREFIX and text.startswith(DEV_ONLY_PREFIX)
    if is_admin and is_dev:
        reply_text = text[len(DEV_ONLY_PREFIX):].strip() or "(空訊息)"
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        except Exception as e:
            print(f"[ADMIN REPLY FAIL] {uid}: {e}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
