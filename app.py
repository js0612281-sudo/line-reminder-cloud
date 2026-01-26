# app.py - Flask LINE webhook (Render) + Task Triggers
import os
import traceback
from flask import Flask, request, abort, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent

# 匯入原本的邏輯
from sheets_utils import upsert_patient
import daily_push
import monthly_stats

# --- 必填環境變數 ---
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET", "").strip()
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN", "").strip()
# 保護排程觸發的密鑰 (請在 Render Environment 設定)
CRON_SECRET = os.getenv("CRON_SECRET", "my-secret-key") 

if not CHANNEL_SECRET or not CHANNEL_ACCESS_TOKEN:
    raise ValueError("缺少 CHANNEL_SECRET 或 CHANNEL_ACCESS_TOKEN")

# 管理者設定
DEV_ONLY_PREFIX = os.getenv("DEV_ONLY_PREFIX", "#dev")
ADMIN_USER_IDS = {u.strip() for u in os.getenv("ADMIN_USER_IDS", "").split(",") if u.strip()}

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
    """ 觸發每日預約提醒 """
    # 1. 安全檢查
    auth_header = request.headers.get("X-Cron-Secret")
    auth_query = request.args.get("key")
    
    if (auth_header != CRON_SECRET) and (auth_query != CRON_SECRET):
        return jsonify({"error": "Unauthorized", "message": "密鑰錯誤，拒絕執行"}), 401

    # 2. 執行邏輯
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


# --- LINE 事件處理 ---

@handler.add(FollowEvent)
def on_follow(event: FollowEvent):
    """
    當使用者加入好友時觸發：
    只負責記錄資料到 Google Sheet，不發送任何回覆訊息。
    """
    uid = event.source.user_id
    display_name = ""
    try:
        prof = line_bot_api.get_profile(uid)
        display_name = prof.display_name or ""
    except Exception:
        pass

    # 寫進 Google Sheet（若 userId 已存在則只更新 displayName）
    try:
        upsert_patient(display_name, uid)
        print(f"[SHEET UPSERT SUCCESS] {display_name} ({uid})")
    except Exception as e:
        # 默默記錄錯誤 log，不打擾使用者
        print(f"[SHEET UPSERT FAIL] {uid}: {e}")

    # 已移除原本的 reply_message 區塊

@handler.add(MessageEvent, message=TextMessage)
def on_message(event: MessageEvent):
    uid = event.source.user_id
    text = (event.message.text or "").strip()

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
