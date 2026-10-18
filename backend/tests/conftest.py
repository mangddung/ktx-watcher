import os
import sys
from pathlib import Path

# app.main 을 import 하기 전에 테스트용 설정을 넣는다(실제 .env 는 읽지 않는다)
os.environ.update({
    "DISCORD_ADMIN_IDS": "111111111111111111",
    "DISCORD_CLIENT_ID": "test-client",
    "DISCORD_CLIENT_SECRET": "test-client-secret-value",
    "SESSION_SECRET": "x" * 48,
    "PUBLIC_BASE_URL": "http://localhost:14371",
    "DATABASE_URL": "sqlite://",
    "DISCORD_BOT_TOKEN": "",
    "TELEGRAM_BOT_TOKEN": "",
})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
