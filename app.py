# app.py - Flask LINE webhook (Render) + Task Triggers
import os
import traceback
from flask import Flask, request, abort, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent

# 匯入原本的程式邏輯
from sheets_utils import upsert_patient
import daily_push
import monthly_stats

# --- 1. 必填環境變數設定 ---
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET", "").strip()
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN", "").strip()
# 保護排程觸發的密鑰 (需與 Render Environment 及 cron-job.org 設定一致)
CRON_SECRET = os.getenv("CRON_SECRET", "my-secret-key") 

if not CHANNEL_SECRET or not CHANNEL_ACCESS_TOKEN:
    raise ValueError("缺少 CHANNEL_SECRET 或 CHANNEL_ACCESS_TOKEN")

# 管理者設定
DEV_ONLY_PREFIX = os.getenv("DEV_ONLY_PREFIX", "#dev")
ADMIN_USER_IDS = {u.strip() for u in os.getenv("ADMIN_USER_IDS", "").split(",") if u.strip()}

app = Flask(__name__)
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# --- 2. 網站健康檢查入口 (給 Keep Alive 戳的) ---
@app.get("/")
def health():
    return "OK (System is running)"

# --- 3. LINE Webhook 入口 (給 LINE 官方伺服器呼叫) ---
@app.post("/callback")
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

# --- 4. 排程觸發入口 (給外部 cron-job.org 呼叫) ---

@app.get("/tasks/daily-push")
def trigger_daily_push():
    """ 觸發每日預約提醒 """
    # 安全檢查：比對密鑰
    auth_header = request.headers.get("X-Cron-Secret")
    auth_query = request.args.get("key")
    
    if (auth_header != CRON_SECRET) and (auth_query != CRON_SECRET):
        return jsonify({"error": "Unauthorized", "message": "密鑰錯誤，拒絕執行"}), 401

    # 執行每日推播邏輯
    try:
        print("[Task] Starting Daily Push...")
        daily_push.main() 
        return jsonify({"status": "success", "message": "每日推播執行完畢"}), 200
    except Exception as e:
        error_msg = traceback.format_exc()
        print(f"[Task Error] {error_msg}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.get("/tasks/monthly-stats")
def trigger_monthly_stats():
    """ 觸發月度統計 (程式內部會自己判斷是不是1號) """
    auth_header = request.headers.get("X-Cron-Secret")
    auth_query = request.args.get("key")

    if (auth_header != CRON_SECRET) and (auth_query != CRON_SECRET):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        print("[Task] Starting Monthly Stats...")
        monthly_stats.main()
        return jsonify({"status": "success", "message": "月度檢查執行完畢"}), 200
    except Exception as e:
        error_msg = traceback.format_exc()
        print(f"[Task Error] {error_msg}")
        return jsonify({"status": "error", "message": str(e)}), 500


# --- 5. LINE 事件處理邏輯 ---

@handler.add(FollowEvent)
def on_follow(event: FollowEvent):
    """
    當使用者【加入好友】時觸發：
    只負責記錄資料到 Google Sheet，不發送任何回覆訊息。
    """
    uid = event.source.user_id
    display_name = ""
    try:
        prof = line_bot_api.get_profile(uid)
        display_name = prof.display_name or ""
    except Exception:
        pass

    # 寫進 Google Sheet
    try:
        upsert_patient(display_name, uid)
        print(f"[SHEET UPSERT SUCCESS] {display_name} ({uid})")
    except Exception as e:
        print(f"[SHEET UPSERT FAIL] {uid}: {e}")

@handler.add(MessageEvent, message=TextMessage)
def on_message(event: MessageEvent):
    """
    當收到【文字訊息】時觸發：
    1. 自動補登：如果是舊病人傳訊息，順便把資料存進 Sheet。
    2. 管理員指令：如果是你傳送 #dev 開頭的訊息，機器人才會回應。
    """
    uid = event.source.user_id
    text = (event.message.text or "").strip()

    # --- A. 自動補登資料 (針對舊病人) ---
    try:
        # 嘗試取得使用者暱稱
        profile = line_bot_api.get_profile(uid)
        display_name = profile.display_name or "Unknown"
        # 存入 Sheet (若已存在，這行會自動忽略，不會重複)
        upsert_patient(display_name, uid)
    except Exception as e:
        # 補登失敗只印 Log，不影響主要流程
        print(f"[AUTO SAVE FAIL] {uid}: {e}")

    # --- B. 管理員測試指令 (#dev) ---
    is_admin = uid in ADMIN_USER_IDS
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
