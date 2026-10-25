"""Local SMTP sink for development: accepts any mail and saves it as .eml in data/mail/.

Database Mail is pointed at localhost:2525, so no test mail ever reaches a real mailbox.
Run: .venv\\Scripts\\python.exe python\\smtp_sink.py
"""
import asyncio
from datetime import datetime
from pathlib import Path

from aiosmtpd.controller import Controller

MAIL_DIR = Path(__file__).resolve().parent.parent / "data" / "mail"
PORT = 2525


class SaveToDisk:
    async def handle_DATA(self, server, session, envelope):
        MAIL_DIR.mkdir(parents=True, exist_ok=True)
        path = MAIL_DIR / f"{datetime.now():%Y%m%d_%H%M%S_%f}.eml"
        path.write_bytes(envelope.original_content)
        print(f"saved {path.name} -> {', '.join(envelope.rcpt_tos)}", flush=True)
        return "250 Message accepted"


if __name__ == "__main__":
    controller = Controller(SaveToDisk(), hostname="127.0.0.1", port=PORT)
    controller.start()
    print(f"SMTP sink listening on 127.0.0.1:{PORT}, writing to {MAIL_DIR}", flush=True)
    try:
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        controller.stop()
