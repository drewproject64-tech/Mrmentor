"""Run Mr Mentor with Python 3.10+ and BOT_TOKEN set."""
import json
import logging
import os
import re
import sqlite3
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DESCRIPTION = (
    "Set a weekly goal for study, productivity, or career development. "
    "Get a daily action, mark it complete, and track your progress in Telegram."
)
ABOUT = "Set a weekly goal, take daily action, and track your progress with Mr Mentor."
GOAL = "Set a Goal"
DAILY = "Daily Action"
PROGRESS = "My Progress"
DONE = "Mark today complete"
COMMANDS = {
    "start": "Open the main menu",
    "help": "See how to use Mr Mentor",
    "goal": "Set a weekly goal",
    "daily": "Get today's action",
    "progress": "View this week's progress",
    "cancel": "Cancel goal setup",
}
COMMAND_ACTIONS = {"/goal": GOAL, "/daily": DAILY, "/progress": PROGRESS}
ACTIONS = {
    "Study": (
        "Review your notes and write down three points you want to remember.",
        "Spend 15 minutes testing yourself without looking at your notes.",
        "Explain one difficult concept in your own words.",
        "Work through one practice question and review your answer.",
        "Review one mistake and write down what you learned.",
        "Summarize this week's material in five sentences.",
        "Choose one topic to revisit next week and schedule time for it.",
    ),
    "Productivity": (
        "Write down the smallest useful step and spend 15 minutes on it.",
        "Block 15 minutes for your goal and remove one distraction.",
        "Identify one obstacle and do something to make it easier to start.",
        "Finish one small piece of work you have been putting off.",
        "Check what you planned and adjust your next step.",
        "Set up the materials or workspace you need for your next session.",
        "Write down what worked this week and choose one habit to repeat.",
    ),
    "Career": (
        "Identify one skill your goal requires and spend 15 minutes practicing it.",
        "Review a relevant job listing and note one skill you could demonstrate.",
        "Write a short example of an achievement that relates to your goal.",
        "Spend 15 minutes improving one item in your CV or portfolio.",
        "Draft one specific question to ask someone working in your field.",
        "Create a small work sample or improve one you already have.",
        "Review your progress and pick one career action for next week.",
    ),
}


def keyboard(*rows):
    return {"keyboard": [[{"text": item} for item in row] for row in rows],
            "resize_keyboard": True}


MENU = keyboard((GOAL, DAILY), (PROGRESS,))


class TelegramError(Exception):
    pass


def telegram_call(token, method, **payload):
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=40) as response:
            result = json.load(response)
    except HTTPError as error:
        with error:
            raise TelegramError(f"{method} failed (HTTP {error.code})") from None
    except (URLError, TimeoutError, OSError):
        raise TelegramError(f"{method} could not reach Telegram") from None
    if not result.get("ok"):
        raise TelegramError(f"{method} was rejected by Telegram")
    return result["result"]


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS goals (
            chat_id INTEGER PRIMARY KEY,
            category TEXT NOT NULL,
            target TEXT NOT NULL,
            week TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS goal_setup (
            chat_id INTEGER PRIMARY KEY,
            category TEXT
        );
        CREATE TABLE IF NOT EXISTS completions (
            chat_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            PRIMARY KEY (chat_id, day)
        );
    """)


def handle(db, chat_id, text, today):
    week = (today - timedelta(days=today.weekday())).isoformat()
    if text.startswith("/"):
        command = text.split(maxsplit=1)[0].partition("@")[0].lower()
        text = COMMAND_ACTIONS.get(command, command)
    if text in ("/start", "/help"):
        db.execute("DELETE FROM goal_setup WHERE chat_id = ?", (chat_id,))
        return (
            "Welcome to Mr Mentor. Use /goal to set a weekly target, /daily to get "
            "today's action, and /progress to see how many days you've completed.",
            MENU,
        )

    if text == GOAL:
        db.execute("REPLACE INTO goal_setup VALUES (?, NULL)", (chat_id,))
        return "Choose a category for your weekly goal.", keyboard(tuple(ACTIONS), ("/cancel",))

    if text == "/cancel":
        db.execute("DELETE FROM goal_setup WHERE chat_id = ?", (chat_id,))
        return "Goal setup closed.", MENU

    if text.startswith("/"):
        db.execute("DELETE FROM goal_setup WHERE chat_id = ?", (chat_id,))
        return "Use /help to see the available commands.", MENU

    goal = db.execute(
        "SELECT category, target FROM goals WHERE chat_id = ? AND week = ?",
        (chat_id, week),
    ).fetchone()

    if text in (DAILY, PROGRESS, DONE):
        db.execute("DELETE FROM goal_setup WHERE chat_id = ?", (chat_id,))
        if goal is None:
            return "Use Set a Goal to choose a target for this week.", MENU

        category, target = goal
        if text == PROGRESS:
            count = db.execute(
                "SELECT COUNT(*) FROM completions WHERE chat_id = ? AND day BETWEEN ? AND ?",
                (chat_id, week, today.isoformat()),
            ).fetchone()[0]
            return f"This week's goal\n{target}\n\nDays completed this week: {count}", MENU

        if text == DONE:
            db.execute("INSERT OR IGNORE INTO completions VALUES (?, ?)", (chat_id, today.isoformat()))
            return "Today's action is marked complete.", MENU

        completed = db.execute(
            "SELECT 1 FROM completions WHERE chat_id = ? AND day = ?",
            (chat_id, today.isoformat()),
        ).fetchone()
        if completed:
            return "You've already completed today's action. Come back tomorrow for the next one.", MENU
        action = ACTIONS[category][today.weekday()]
        return f"Your goal\n{target}\n\nToday's action\n{action}", keyboard((DONE,), (GOAL, DAILY), (PROGRESS,))

    setup = db.execute("SELECT category FROM goal_setup WHERE chat_id = ?", (chat_id,)).fetchone()
    if setup is None:
        return "Choose an option below.", MENU
    if setup[0] is None:
        if text not in ACTIONS:
            return "Choose Study, Productivity, or Career.", keyboard(tuple(ACTIONS), ("/cancel",))
        db.execute("UPDATE goal_setup SET category = ? WHERE chat_id = ?", (text, chat_id))
        return (
            "What will you finish by the end of this week? Give it a clear target, "
            "such as 'Complete three practice exercises by Sunday.'",
            keyboard(("/cancel",)),
        )
    if not 4 <= len(text) <= 240 or text.startswith("/"):
        return "Enter a goal between 4 and 240 characters, or use /cancel.", keyboard(("/cancel",))
    db.execute("REPLACE INTO goals VALUES (?, ?, ?, ?)", (chat_id, setup[0], text, week))
    db.execute("DELETE FROM goal_setup WHERE chat_id = ?", (chat_id,))
    return f"Weekly goal saved\n{text}\n\nTap Daily Action for today's step.", MENU


def start_health_server(port, last_poll):
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/health"):
                self.send_error(404)
                return
            ready = time.monotonic() - last_poll[0] < 90
            self.send_response(200 if ready else 503)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK\n" if ready else b"Telegram polling unavailable\n")

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    logging.info("Health server listening on port %s", port)
    return server


def poll(token, last_poll):
    offset = 0
    initialized = False
    profile_steps = [
        ("setMyName", {"name": "Mr Mentor"}),
        ("setMyDescription", {"description": DESCRIPTION}),
        ("setMyShortDescription", {"short_description": ABOUT}),
        ("setMyCommands", {
            "commands": [
                {"command": name, "description": description}
                for name, description in COMMANDS.items()
            ],
        }),
    ]
    retry_profile_at = 0
    with sqlite3.connect(os.environ.get("BOT_DB_PATH", "mrmentor.sqlite3")) as db:
        initialize(db)
        while True:
            try:
                if not initialized:
                    identity = telegram_call(token, "getMe")
                    telegram_call(token, "deleteWebhook", drop_pending_updates=False)
                    logging.info("Starting Telegram polling for @%s", identity["username"])
                    initialized = True
                updates = telegram_call(
                    token, "getUpdates", offset=offset, timeout=20, allowed_updates=["message"]
                )
                last_poll[0] = time.monotonic()
                for update in updates:
                    message = update.get("message", {})
                    chat = message.get("chat", {})
                    if chat.get("type") == "private" and isinstance(message.get("text"), str):
                        with db:
                            reply, markup = handle(db, chat["id"], message["text"].strip(), date.today())
                            try:
                                telegram_call(token, "sendMessage", chat_id=chat["id"],
                                              text=reply, reply_markup=markup)
                            except TelegramError as error:
                                if "HTTP 403" not in str(error) and "HTTP 400" not in str(error):
                                    raise
                                logging.warning("%s", error)
                    offset = update["update_id"] + 1
            except TelegramError as error:
                logging.warning("%s. Retrying in five seconds.", error)
                time.sleep(5)
                continue

            if profile_steps and time.monotonic() >= retry_profile_at:
                method, fields = profile_steps.pop(0)
                try:
                    telegram_call(token, method, **fields)
                except TelegramError as error:
                    logging.warning("%s. Profile setup will retry while the bot keeps polling.", error)
                    profile_steps.append((method, fields))
                    retry_profile_at = time.monotonic() + 60


def run():
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
        raise SystemExit("Set BOT_TOKEN to the token provided by BotFather.")

    last_poll = [float("-inf")]
    port = os.environ.get("PORT")
    if not port:
        poll(token, last_poll)
        return

    with start_health_server(int(port), last_poll) as server:
        try:
            poll(token, last_poll)
        finally:
            server.shutdown()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
