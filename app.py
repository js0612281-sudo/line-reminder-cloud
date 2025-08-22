# app.py - Flask LINE webhook (Render), save patients to Google Sheet
import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent

# 這支工具會把使用者加入 Google 試算表（displayName, realName, userId）
from sheets_utils import upsert_patient

# --- 必填環境變數 ---
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET", "").strip()
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN", "").strip()
if not CHANNEL_SECRET or not CHANNEL_ACCESS_TOKEN:
    raise ValueError("缺少 CHANNEL_SECRET 或 CHANNEL_ACCESS_TOKEN（請到 Render > Settings > Environment 加上）")

# 只有管理者、而且訊息以這個前綴開頭才會回覆（避免打擾病人）
DEV_ONLY_PREFIX = os.getenv("DEV_ONLY_PREFIX", "#dev")
ADMIN_USER_IDS = {u.strip() for u in os.getenv("ADMIN_USER_IDS", "").split(",") if u.strip()}

app = Flask(__name__)
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

@app.get("/")
def health():
    return "OK"

@app.post("/callback")
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

# 使用者「加入好友」時觸發：記錄 displayName + userId 到 Google Sheet
@handler.add(FollowEvent)
def on_follow(event: FollowEvent):
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
    except Exception as e:
        # 不影響使用者體驗，但在 log 記下
        print(f"[SHEET UPSERT FAIL] {uid}: {e}")

    # 可選：回一則簡短訊息（若完全不想回可註解掉）
    try:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="加入成功 ✅ 我們已記錄您的顯示名稱；治療師稍後會更新為您的正式姓名。")
        )
    except Exception as e:
        print(f"[FOLLOW REPLY FAIL] {uid}: {e}")

# 訊息事件：只有「管理者 + 以 #dev 開頭」才回覆；其他一律靜默（避免打擾病人）
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
    # 非 admin 或沒有 #dev 前綴：不回覆

if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port)