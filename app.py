import os
import csv
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

PATIENTS_CSV = "patients.csv"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    display_name = line_bot_api.get_profile(user_id).display_name
    text = event.message.text.strip()

    # 紀錄 userId
    updated = False
    rows = []
    if os.path.exists(PATIENTS_CSV):
        with open(PATIENTS_CSV, newline='', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))

    exists = False
    for row in rows:
        if row['userId'] == user_id:
            exists = True
            break
    if not exists:
        rows.append({'displayName': display_name, 'realName': '', 'userId': user_id})
        with open(PATIENTS_CSV, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['displayName','realName','userId'])
            writer.writeheader()
            writer.writerows(rows)

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="收到訊息: " + text))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
