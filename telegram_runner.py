"""
OBI Telegram Command Runner
Entry point for the GitHub Actions command-polling workflow.
Run independently from main.py (the cron signal pipeline).
"""
from agents.telegram_command_agent import poll_and_process

if __name__ == "__main__":
    print("[CMD] OBI Telegram Command Runner starting…")
    poll_and_process()
    print("[CMD] Done.")
