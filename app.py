import os
import re
import threading
import uuid
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_from_directory, abort

load_dotenv()

from automation.orangehrm_bot import OrangeHRMBot, EmployeeInput

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
LOG_DIR = os.path.join(BASE_DIR, "logs")
HEADLESS = os.getenv("HEADLESS", "true").lower() != "false"

app = Flask(__name__)
app.json.sort_keys = False

JOBS = {}
JOBS_LOCK = threading.Lock()
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .'-]{0,29}$")
EMP_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,10}$")


def validate(form: dict) -> list:
    errors = []
    if not form.get("username"):
        errors.append("Username is required.")
    if not form.get("password"):
        errors.append("Password is required.")
    if not NAME_RE.match(form.get("first_name", "")):
        errors.append("First name is required (letters only, max 30).")
    if not NAME_RE.match(form.get("last_name", "")):
        errors.append("Last name is required (letters only, max 30).")
    if form.get("middle_name") and not NAME_RE.match(form["middle_name"]):
        errors.append("Middle name may contain letters only (max 30).")
    if not EMP_ID_RE.match(form.get("employee_id", "")):
        errors.append("Employee ID is required (alphanumeric, max 10 characters).")
    return errors


def run_job(run_id: str, form: dict):
    def on_step(step):
        with JOBS_LOCK:
            steps = JOBS[run_id]["steps"]
            if steps and steps[-1]["step"] == step["step"]:
                steps[-1] = step
            else:
                steps.append(step)

    employee = EmployeeInput(
        first_name=form["first_name"],
        middle_name=form.get("middle_name", ""),
        last_name=form["last_name"],
        employee_id=form["employee_id"],
    )
    bot = OrangeHRMBot(
        username=form["username"],
        password=form["password"],
        employee=employee,
        run_id=run_id,
        output_dir=OUTPUT_DIR,
        log_dir=LOG_DIR,
        headless=HEADLESS,
        max_pages=int(form.get("max_pages") or 1),
        on_step=on_step,
    )
    result = bot.run()
    with JOBS_LOCK:
        JOBS[run_id].update(status="success" if result.success else "failed", result=result.to_dict())


@app.route("/")
def index():
    return render_template("index.html", default_username=os.getenv("ORANGEHRM_USERNAME", ""))


@app.route("/api/run", methods=["POST"])
def start_run():
    form = {k: (v or "").strip() if isinstance(v, str) else v
            for k, v in (request.get_json(silent=True) or {}).items()}
    form["username"] = form.get("username") or os.getenv("ORANGEHRM_USERNAME", "")
    form["password"] = form.get("password") or os.getenv("ORANGEHRM_PASSWORD", "")

    errors = validate(form)
    if errors:
        return jsonify({"errors": errors}), 400

    with JOBS_LOCK:
        if any(j["status"] == "running" for j in JOBS.values()):
            return jsonify({"errors": ["Another automation run is already in progress."]}), 409
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
        JOBS[run_id] = {"status": "running", "steps": [], "result": None}

    threading.Thread(target=run_job, args=(run_id, form), daemon=True).start()
    return jsonify({"run_id": run_id}), 202


@app.route("/api/status/<run_id>")
def status(run_id):
    with JOBS_LOCK:
        job = JOBS.get(run_id)
        if not job:
            abort(404)
        return jsonify({"run_id": run_id, **job})


@app.route("/api/logs/<run_id>")
def logs(run_id):
    if not re.fullmatch(r"[\w]+", run_id):
        abort(400)
    path = os.path.join(LOG_DIR, f"automation_{run_id}.log")
    if not os.path.exists(path):
        return jsonify({"lines": []})
    with open(path, encoding="utf-8") as f:
        return jsonify({"lines": f.read().splitlines()[-200:]})


@app.route("/files/<path:filename>")
def files(filename):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=filename.endswith((".csv", ".xlsx")))


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=False)
