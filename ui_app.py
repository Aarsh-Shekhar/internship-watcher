# ui_app.py
import os
from flask import Flask, render_template, redirect, url_for, flash, request
from watch_core import run_scan, load_feed

app = Flask(__name__)
app.secret_key = "dev"  # for flash messages


def _last_log_line():
    try:
        with open(
            os.path.expanduser("~/Library/Logs/internship-watcher.out.log"),
            "r",
            encoding="utf-8",
        ) as f:
            lines = f.readlines()
        return lines[-1].strip() if lines else ""
    except Exception:
        return ""


@app.get("/")
def index():
    # How many rows to show (default 10). You can change via /?n=25 etc.
    limit = max(1, min(int(request.args.get("n", "10")), 200))
    feed = load_feed()[:limit]  # newest first
    last = _last_log_line()
    return render_template("index.html", feed=feed, last_line=last, limit=limit)


@app.post("/run-now")
def run_now():
    # Force a push even when nothing new, so you get a banner: "Manual check: You're all caught up"
    items, kept = run_scan(
        seed=False, notify_when_zero=True, zero_prefix="Manual check"
    )
    if kept:
        flash(f"Found {kept} new internship(s). Notifications sent.")
    else:
        flash("You're all caught up — no new internships.")
    return redirect(url_for("index"))


@app.get("/logs")
def logs():
    try:
        path = os.path.expanduser("~/Library/Logs/internship-watcher.out.log")
        with open(path, "r", encoding="utf-8") as f:
            content = "".join(f.readlines()[-200:])
    except Exception:
        content = "(no logs yet)"
    return f"<pre style='white-space:pre-wrap;margin:0;padding:16px;font:13px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace'>{content}</pre>"


@app.get("/test-ping")
def test_ping():
    from watch_core import notify_mac, phone_notify

    notify_mac("Internship Watcher", "UI test ping")
    phone_notify("Internship Watcher", "UI test ping", priority=2)
    return "ok"


if __name__ == "__main__":
    # pip install flask
    app.run(host="127.0.0.1", port=5000, debug=False)
