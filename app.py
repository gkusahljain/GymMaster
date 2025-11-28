from flask import Flask, render_template, request, redirect, url_for, flash, session, Response
import mysql.connector
from datetime import datetime, date
import re
from functools import wraps
import csv
import io
from werkzeug.security import check_password_hash

app = Flask(__name__)
app.secret_key = "supersecretkey"  # change for production

# ---------- DB CONFIG ----------
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "Kushal@1426",   # your MySQL password
    "database": "gymmaster"
}


def get_connection():
    return mysql.connector.connect(**DB_CONFIG)


# ---------- AUTH HELPERS ----------

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            flash("You do not have permission to access this page.", "danger")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return wrapper


# ---------- VALIDATION HELPERS ----------

def is_valid_phone(phone: str) -> bool:
    return bool(re.fullmatch(r"[6-9]\d{9}", phone))


def is_valid_email(email: str) -> bool:
    return bool(re.fullmatch(r"[^@]+@[^@]+\.[^@]+", email))


def bmi_category(bmi):
    if bmi is None:
        return "Unknown"
    bmi = float(bmi)
    if bmi < 18.5:
        return "Underweight"
    if bmi < 25:
        return "Normal"
    if bmi < 30:
        return "Overweight"
    return "Obese"


# ---------- AUTH ROUTES ----------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        conn = get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cur.fetchone()
        conn.close()

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid username or password.", "danger")
            return render_template("login.html")

        session["user_id"] = user["user_id"]
        session["username"] = user["username"]
        session["role"] = user["role"]

        flash("Logged in successfully.", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))


# ---------- DASHBOARD (DATE + CHARTS) ----------

@app.route("/", methods=["GET", "POST"])
@admin_required
def dashboard():
    if request.method == "POST":
        selected_date = request.form.get("date") or date.today().isoformat()
    else:
        selected_date = date.today().isoformat()

    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    # Counts
    cur.execute("SELECT COUNT(*) AS c FROM members")
    total_members = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM trainers")
    total_trainers = cur.fetchone()["c"]

    # Attendance & revenue for selected date
    cur.execute(
        "SELECT COUNT(*) AS c FROM attendance WHERE DATE(checkin_time) = %s",
        (selected_date,)
    )
    day_attendance = cur.fetchone()["c"]

    cur.execute("""
        SELECT IFNULL(SUM(amount), 0) AS total
        FROM payments
        WHERE status = 'paid' AND payment_date = %s
    """, (selected_date,))
    day_revenue = float(cur.fetchone()["total"])

    # Recent activities
    cur.execute("""
        SELECT a.checkin_time, m.name
        FROM attendance a
        JOIN members m ON a.member_id = m.member_id
        ORDER BY a.checkin_time DESC
        LIMIT 5
    """)
    recent_attendance = cur.fetchall()

    cur.execute("""
        SELECT p.payment_date, p.amount, m.name AS member_name
        FROM payments p
        JOIN members m ON p.member_id = m.member_id
        ORDER BY p.payment_date DESC, p.payment_id DESC
        LIMIT 5
    """)
    recent_payments = cur.fetchall()

    # Members joined per month (last 6)
    cur.execute("""
        SELECT DATE_FORMAT(join_date, '%Y-%m') AS ym, COUNT(*) AS c
        FROM members
        GROUP BY ym
        ORDER BY ym
        LIMIT 6
    """)
    rows = cur.fetchall()
    member_month_labels = [r["ym"] for r in rows]
    member_month_values = [r["c"] for r in rows]

    # Revenue per month (last 6, paid only)
    cur.execute("""
        SELECT DATE_FORMAT(payment_date, '%Y-%m') AS ym, IFNULL(SUM(amount),0) AS total
        FROM payments
        WHERE status = 'paid'
        GROUP BY ym
        ORDER BY ym
        LIMIT 6
    """)
    rows = cur.fetchall()
    revenue_month_labels = [r["ym"] for r in rows]
    revenue_month_values = [float(r["total"]) for r in rows]

    # Attendance last 7 days
    cur.execute("""
        SELECT DATE(checkin_time) AS d, COUNT(*) AS c
        FROM attendance
        WHERE DATE(checkin_time) >= DATE_SUB(CURDATE(), INTERVAL 6 DAY)
        GROUP BY d
        ORDER BY d
    """)
    rows = cur.fetchall()
    att_labels = [r["d"].strftime("%Y-%m-%d") for r in rows]
    att_values = [r["c"] for r in rows]

    conn.close()
    return render_template(
        "dashboard.html",
        total_members=total_members,
        total_trainers=total_trainers,
        day_attendance=day_attendance,
        day_revenue=day_revenue,
        selected_date=selected_date,
        recent_attendance=recent_attendance,
        recent_payments=recent_payments,
        member_month_labels=member_month_labels,
        member_month_values=member_month_values,
        revenue_month_labels=revenue_month_labels,
        revenue_month_values=revenue_month_values,
        att_labels=att_labels,
        att_values=att_values
    )


# ---------- MEMBERS ----------

@app.route("/members")
@admin_required
def members():
    q = (request.args.get("q") or "").strip()

    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    base_sql = """
        SELECT m.*,
               (SELECT COUNT(*) FROM attendance a WHERE a.member_id = m.member_id) AS visit_count,
               (SELECT t.name FROM trainers t
                    JOIN member_trainer mt ON t.trainer_id = mt.trainer_id
                    WHERE mt.member_id = m.member_id
                    LIMIT 1) AS trainer_name,
               CASE m.membership_plan
                    WHEN 'Monthly'   THEN DATE_ADD(m.join_date, INTERVAL 1 MONTH)
                    WHEN 'Quarterly' THEN DATE_ADD(m.join_date, INTERVAL 3 MONTH)
                    WHEN 'Yearly'    THEN DATE_ADD(m.join_date, INTERVAL 12 MONTH)
                    ELSE NULL
               END AS membership_end_date,
               CASE
                    WHEN (
                        CASE m.membership_plan
                            WHEN 'Monthly'   THEN DATE_ADD(m.join_date, INTERVAL 1 MONTH)
                            WHEN 'Quarterly' THEN DATE_ADD(m.join_date, INTERVAL 3 MONTH)
                            WHEN 'Yearly'    THEN DATE_ADD(m.join_date, INTERVAL 12 MONTH)
                            ELSE NULL
                        END
                    ) < CURDATE()
                    THEN 'Expired'
                    WHEN (
                        CASE m.membership_plan
                            WHEN 'Monthly'   THEN DATE_ADD(m.join_date, INTERVAL 1 MONTH)
                            WHEN 'Quarterly' THEN DATE_ADD(m.join_date, INTERVAL 3 MONTH)
                            WHEN 'Yearly'    THEN DATE_ADD(m.join_date, INTERVAL 12 MONTH)
                            ELSE NULL
                        END
                    ) BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 7 DAY)
                    THEN 'Expiring Soon'
                    ELSE 'Active'
               END AS membership_status
        FROM members m
    """

    if q:
        like = f"%{q}%"
        sql = base_sql + " WHERE m.name LIKE %s OR m.phone LIKE %s OR m.email LIKE %s ORDER BY m.join_date DESC"
        cur.execute(sql, (like, like, like))
    else:
        sql = base_sql + " ORDER BY m.join_date DESC"
        cur.execute(sql)

    members = cur.fetchall()
    conn.close()
    return render_template("members.html", members=members, q=q)


@app.route("/members/add", methods=["GET", "POST"])
@admin_required
def add_member():
    form = {
        "name": "",
        "phone": "",
        "email": "",
        "height_cm": "",
        "membership_plan": "",
        "goal": "",
    }

    if request.method == "POST":
        form["name"] = (request.form.get("name") or "").strip()
        form["phone"] = (request.form.get("phone") or "").strip()
        form["email"] = (request.form.get("email") or "").strip()
        form["height_cm"] = (request.form.get("height_cm") or "").strip()
        form["membership_plan"] = (request.form.get("membership_plan") or "").strip()
        form["goal"] = (request.form.get("goal") or "").strip()

        errors = []

        if not form["name"]:
            errors.append("Name is required.")

        if not form["phone"]:
            errors.append("Phone number is required.")
        elif not is_valid_phone(form["phone"]):
            errors.append("Phone number must be a valid 10-digit Indian mobile number starting with 6–9.")

        if not form["email"]:
            errors.append("Email address is required.")
        elif not is_valid_email(form["email"]):
            errors.append("Please enter a valid email address.")

        if not form["height_cm"]:
            errors.append("Height (in cm) is required.")
        else:
            try:
                h = float(form["height_cm"])
                if h <= 0 or h > 300:
                    errors.append("Height must be a positive number less than 300 cm.")
            except ValueError:
                errors.append("Height must be a valid number.")

        if not form["membership_plan"]:
            errors.append("Please select a membership plan.")

        if not form["goal"]:
            errors.append("Please select a fitness goal.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("member_form.html", form=form)

        join_date = date.today().isoformat()
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO members (name, phone, email, join_date, height_cm, membership_plan, goal)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            form["name"],
            form["phone"],
            form["email"],
            join_date,
            form["height_cm"],
            form["membership_plan"],
            form["goal"],
        ))
        conn.commit()
        conn.close()
        flash("Member added successfully!", "success")
        return redirect(url_for("members"))

    return render_template("member_form.html", form=form)


@app.route("/members/<int:member_id>/delete", methods=["POST"])
@admin_required
def delete_member(member_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM attendance WHERE member_id = %s", (member_id,))
    cur.execute("DELETE FROM payments WHERE member_id = %s", (member_id,))
    cur.execute("DELETE FROM health_metrics WHERE member_id = %s", (member_id,))
    cur.execute("DELETE FROM workout_recommendations WHERE member_id = %s", (member_id,))
    cur.execute("DELETE FROM member_trainer WHERE member_id = %s", (member_id,))
    cur.execute("DELETE FROM members WHERE member_id = %s", (member_id,))
    conn.commit()
    conn.close()
    flash("Member and related records deleted.", "info")
    return redirect(url_for("members"))


# ---------- TRAINERS ----------

@app.route("/trainers")
@admin_required
def trainers():
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM trainers ORDER BY trainer_id DESC")
    trainers = cur.fetchall()
    conn.close()
    return render_template("trainers.html", trainers=trainers)


@app.route("/trainers/add", methods=["GET", "POST"])
@admin_required
def add_trainer():
    form = {
        "name": "",
        "specialization": "",
        "phone": "",
    }

    if request.method == "POST":
        form["name"] = (request.form.get("name") or "").strip()
        form["specialization"] = (request.form.get("specialization") or "").strip()
        form["phone"] = (request.form.get("phone") or "").strip()

        errors = []
        if not form["name"]:
            errors.append("Trainer name is required.")
        if form["phone"] and not is_valid_phone(form["phone"]):
            errors.append("Trainer phone must be a valid 10-digit Indian mobile number starting with 6–9.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("trainer_form.html", form=form)

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO trainers (name, specialization, phone)
            VALUES (%s, %s, %s)
        """, (form["name"], form["specialization"] or None, form["phone"] or None))
        conn.commit()
        conn.close()
        flash("Trainer added successfully!", "success")
        return redirect(url_for("trainers"))

    return render_template("trainer_form.html", form=form)


@app.route("/trainers/<int:trainer_id>/delete", methods=["POST"])
@admin_required
def delete_trainer(trainer_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM member_trainer WHERE trainer_id = %s", (trainer_id,))
    cur.execute("DELETE FROM trainers WHERE trainer_id = %s", (trainer_id,))
    conn.commit()
    conn.close()
    flash("Trainer deleted.", "info")
    return redirect(url_for("trainers"))


# ---------- ASSIGN TRAINER ----------

@app.route("/assign-trainer", methods=["GET", "POST"])
@admin_required
def assign_trainer():
    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    cur.execute("SELECT member_id, name FROM members ORDER BY name")
    members = cur.fetchall()
    cur.execute("SELECT trainer_id, name, specialization FROM trainers ORDER BY name")
    trainers = cur.fetchall()

    if request.method == "POST":
        member_id = request.form.get("member_id")
        trainer_id = request.form.get("trainer_id")
        assigned_date = date.today().isoformat()

        if not member_id or not trainer_id:
            flash("Please select both member and trainer.", "danger")
            conn.close()
            return render_template("assign_trainer.html", members=members, trainers=trainers)

        cur2 = conn.cursor()
        cur2.execute(
            "SELECT member_id FROM member_trainer WHERE member_id = %s",
            (member_id,)
        )
        existing = cur2.fetchone()
        if existing:
            cur2.execute("""
                UPDATE member_trainer
                SET trainer_id = %s, assigned_date = %s
                WHERE member_id = %s
            """, (trainer_id, assigned_date, member_id))
            flash("Trainer updated for member.", "info")
        else:
            cur2.execute("""
                INSERT INTO member_trainer (member_id, trainer_id, assigned_date)
                VALUES (%s, %s, %s)
            """, (member_id, trainer_id, assigned_date))
            flash("Trainer assigned to member.", "success")

        conn.commit()
        conn.close()
        return redirect(url_for("assign_trainer"))

    conn.close()
    return render_template("assign_trainer.html", members=members, trainers=trainers)


# ---------- ATTENDANCE ----------

@app.route("/attendance", methods=["GET", "POST"])
@admin_required
def attendance():
    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    if request.method == "POST":
        selected_date = request.form.get("date") or date.today().isoformat()
    else:
        selected_date = date.today().isoformat()

    cur.execute("""
        SELECT a.checkin_time, m.name, m.member_id
        FROM attendance a
        JOIN members m ON a.member_id = m.member_id
        WHERE DATE(a.checkin_time) = %s
        ORDER BY a.checkin_time DESC
    """, (selected_date,))
    records = cur.fetchall()

    conn.close()
    return render_template("attendance.html",
                           records=records,
                           selected_date=selected_date)


@app.route("/attendance/mark", methods=["GET", "POST"])
@admin_required
def mark_attendance():
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT member_id, name FROM members ORDER BY name")
    members = cur.fetchall()

    if request.method == "POST":
        member_id = request.form.get("member_id")
        if not member_id:
            flash("Please select a member.", "danger")
            conn.close()
            return render_template("attendance_form.html", members=members)

        checkin_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur2 = conn.cursor()
        cur2.execute("""
            INSERT INTO attendance (member_id, checkin_time)
            VALUES (%s, %s)
        """, (member_id, checkin_time))
        conn.commit()
        conn.close()

        flash(f"Attendance marked at {checkin_time}", "success")
        return redirect(url_for("attendance"))

    conn.close()
    return render_template("attendance_form.html", members=members)


@app.route("/attendance/export")
@admin_required
def export_attendance():
    selected_date = request.args.get("date") or date.today().isoformat()

    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT a.checkin_time, m.member_id, m.name
        FROM attendance a
        JOIN members m ON a.member_id = m.member_id
        WHERE DATE(a.checkin_time) = %s
        ORDER BY a.checkin_time
    """, (selected_date,))
    rows = cur.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Checkin Time", "Member ID", "Member Name"])
    for r in rows:
        writer.writerow([r["checkin_time"], r["member_id"], r["name"]])

    csv_data = output.getvalue()
    output.close()

    filename = f"attendance_{selected_date}.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )


# ---------- PAYMENTS ----------

@app.route("/payments")
@admin_required
def payments():
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT p.payment_id, p.member_id, p.amount, p.payment_date,
               p.payment_mode, p.status,
               m.name AS member_name
        FROM payments p
        JOIN members m ON p.member_id = m.member_id
        ORDER BY p.payment_date DESC, p.payment_id DESC
    """)
    rows = cur.fetchall()
    conn.close()
    return render_template("payments.html", payments=rows)


@app.route("/payments/add", methods=["GET", "POST"])
@admin_required
def add_payment():
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT member_id, name FROM members ORDER BY name")
    members = cur.fetchall()

    if request.method == "POST":
        member_id = request.form.get("member_id")
        amount_raw = (request.form.get("amount") or "").strip()
        mode = (request.form.get("payment_mode") or "").strip()
        status = (request.form.get("status") or "").strip() or "paid"

        errors = []
        if not member_id:
            errors.append("Please select a member.")
        if not amount_raw:
            errors.append("Amount is required.")
        else:
            try:
                amount = float(amount_raw)
                if amount <= 0:
                    errors.append("Amount must be greater than zero.")
            except ValueError:
                errors.append("Amount must be a valid number.")

        if not mode:
            errors.append("Please select a payment mode.")

        if errors:
            for e in errors:
                flash(e, "danger")
            conn.close()
            return render_template("payment_form.html", members=members)

        payment_date = date.today().isoformat()
        conn2 = get_connection()
        cur2 = conn2.cursor()
        cur2.execute("""
            INSERT INTO payments (member_id, amount, payment_date, payment_mode, status)
            VALUES (%s, %s, %s, %s, %s)
        """, (member_id, amount_raw, payment_date, mode, status))
        conn2.commit()
        conn2.close()

        flash("Payment recorded successfully.", "success")
        return redirect(url_for("payments"))

    conn.close()
    return render_template("payment_form.html", members=members)


@app.route("/payments/<int:payment_id>/delete", methods=["POST"])
@admin_required
def delete_payment(payment_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM payments WHERE payment_id = %s", (payment_id,))
    conn.commit()
    conn.close()
    flash("Payment deleted.", "info")
    return redirect(url_for("payments"))


# ---------- HEALTH METRICS & TRACKING ----------

@app.route("/members/<int:member_id>/health")
@admin_required
def health_metrics(member_id):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    cur.execute("SELECT * FROM members WHERE member_id = %s", (member_id,))
    member = cur.fetchone()
    if not member:
        conn.close()
        flash("Member not found.", "danger")
        return redirect(url_for("members"))

    cur.execute("""
        SELECT t.name FROM trainers t
        JOIN member_trainer mt ON t.trainer_id = mt.trainer_id
        WHERE mt.member_id = %s
    """, (member_id,))
    trainer_row = cur.fetchone()
    trainer = trainer_row["name"] if trainer_row else None

    cur.execute("""
        SELECT * FROM health_metrics
        WHERE member_id = %s
        ORDER BY record_date DESC, metric_id DESC
    """, (member_id,))
    metrics = cur.fetchall()

    summary = None
    if metrics:
        latest = metrics[0]
        earliest = metrics[-1]
        latest_bmi = latest["bmi"]
        latest_weight = latest["weight_kg"]
        weight_change = None
        if latest_weight is not None and earliest["weight_kg"] is not None:
            weight_change = round(float(latest_weight) - float(earliest["weight_kg"]), 1)

        category = bmi_category(latest_bmi)
        progress_text = "Not enough data yet."

        if weight_change is not None and member.get("goal"):
            goal = member["goal"]
            if goal == "weight_loss":
                if weight_change < -1:
                    progress_text = f"Good progress: {abs(weight_change)} kg lost."
                elif weight_change > 1:
                    progress_text = f"Moving away from goal: {weight_change} kg gained."
                else:
                    progress_text = "Weight is relatively stable."
            elif goal == "muscle_gain":
                if weight_change > 1:
                    progress_text = f"Good progress: {weight_change} kg gained."
                elif weight_change < -1:
                    progress_text = f"Moving away from goal: {abs(weight_change)} kg lost."
                else:
                    progress_text = "Weight is relatively stable."
            else:
                progress_text = "Goal: general fitness; keep consistency."

        cur.execute("""
            SELECT COUNT(*) AS visits,
                   MAX(checkin_time) AS last_visit
            FROM attendance
            WHERE member_id = %s
        """, (member_id,))
        att = cur.fetchone()

        summary = {
            "latest_bmi": latest_bmi,
            "bmi_category": category,
            "latest_weight": latest_weight,
            "weight_change": weight_change,
            "progress_text": progress_text,
            "last_record_date": latest["record_date"],
            "total_visits": att["visits"] if att and att["visits"] is not None else 0,
            "last_visit": att["last_visit"],
        }

    cur.execute("""
        SELECT * FROM workout_recommendations
        WHERE member_id = %s
        ORDER BY recommendation_date DESC, rec_id DESC
        LIMIT 5
    """, (member_id,))
    recs = cur.fetchall()

    conn.close()
    return render_template(
        "health_metrics.html",
        member=member,
        metrics=metrics,
        recs=recs,
        summary=summary,
        trainer=trainer
    )


@app.route("/members/<int:member_id>/health/add", methods=["GET", "POST"])
@admin_required
def add_health_metric(member_id):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM members WHERE member_id = %s", (member_id,))
    member = cur.fetchone()
    if not member:
        conn.close()
        flash("Member not found.", "danger")
        return redirect(url_for("members"))

    if request.method == "POST":
        weight = (request.form.get("weight") or "").strip()
        bmi = (request.form.get("bmi") or "").strip()
        hr = (request.form.get("hr") or "").strip()

        errors = []

        weight_kg = None
        bmi_val = None
        hr_val = None

        if weight:
            try:
                weight_kg = float(weight)
                if weight_kg <= 0 or weight_kg > 500:
                    errors.append("Weight must be between 0 and 500 kg.")
            except ValueError:
                errors.append("Weight must be a valid number.")

        if bmi:
            try:
                bmi_val = float(bmi)
                if bmi_val <= 5 or bmi_val > 80:
                    errors.append("BMI must be between 5 and 80.")
            except ValueError:
                errors.append("BMI must be a valid number.")

        if hr:
            try:
                hr_val = int(hr)
                if hr_val < 30 or hr_val > 220:
                    errors.append("Heart rate must be between 30 and 220 bpm.")
            except ValueError:
                errors.append("Heart rate must be an integer.")

        if bmi_val is None and weight_kg is not None and member.get("height_cm"):
            try:
                height_m = float(member["height_cm"]) / 100.0
                if height_m > 0:
                    bmi_val = round(weight_kg / (height_m * height_m), 2)
            except Exception:
                pass

        if errors:
            for e in errors:
                flash(e, "danger")
            conn.close()
            return render_template("health_form.html", member=member)

        record_date = date.today().isoformat()

        cur2 = conn.cursor()
        cur2.execute("""
            INSERT INTO health_metrics (member_id, record_date, weight_kg, bmi, resting_heart_rate)
            VALUES (%s, %s, %s, %s, %s)
        """, (member_id, record_date, weight_kg, bmi_val, hr_val))
        conn.commit()
        conn.close()
        flash("Health metrics recorded.", "success")
        return redirect(url_for("health_metrics", member_id=member_id))

    conn.close()
    return render_template("health_form.html", member=member)


# ---------- AI RECOMMENDATIONS ----------

def build_recommendation_text(name, goal, bmi, weight_kg, hr, height_cm):
    lines = []
    lines.append(f"Member: {name}")
    lines.append(f"Goal: {goal}")
    lines.append(f"Height: {height_cm} cm | Weight: {weight_kg} kg | BMI: {bmi} ({bmi_category(bmi)})")
    if hr is not None:
        lines.append(f"Resting Heart Rate: {hr} bpm")
    lines.append("")

    goal_key = goal or "general_fitness"
    bmi_val = bmi if bmi is not None else 23.0

    lines.append("=== Workout Plan (Weekly) ===")

    if bmi_val < 18.5:
        cardio_minutes = 15
        strength_days = 3
    elif bmi_val < 25:
        cardio_minutes = 25
        strength_days = 4
    else:
        cardio_minutes = 35
        strength_days = 3

    if goal_key == "weight_loss":
        lines.append(f"- Focus: Higher {cardio_minutes}–40 min cardio + light strength training.")
        lines.append(f"- Cardio: {cardio_minutes}–40 min brisk walking, treadmill, cycling (4 days/week).")
        lines.append("- Strength: Full-body circuits (2–3 days/week), light-to-moderate weights, 12–15 reps.")
    elif goal_key == "muscle_gain":
        lines.append("- Focus: Progressive overload strength training; low-to-moderate cardio.")
        lines.append(f"- Strength: {strength_days}–5 days/week split (push/pull/legs).")
        lines.append("- Cardio: 15–20 min light cardio 2–3 days/week.")
    else:
        lines.append("- Focus: Balanced routine (strength + cardio).")
        lines.append("- Strength: 3 days/week full body (compound lifts).")
        lines.append("- Cardio: 20–30 min moderate cardio 3 days/week.")

    lines.append("")
    lines.append("=== Sample Vegetarian Meal Plan ===")

    if goal_key == "weight_loss":
        lines.append("Breakfast: Oats with skim milk + nuts OR vegetable upma + soya.")
        lines.append("Lunch: 2 phulkas, dal, sabzi, salad.")
        lines.append("Snack: Green tea + roasted chana or fruit.")
        lines.append("Dinner: 1–2 phulkas/rice + dal/sambar + sabzi + salad.")
        lines.append("Note: Slight calorie deficit, avoid sugary drinks & fried food.")
    elif goal_key == "muscle_gain":
        lines.append("Breakfast: Paneer/soya bhurji + phulkas OR oats + milk + peanut butter + banana.")
        lines.append("Mid-morning: Buttermilk + handful of nuts.")
        lines.append("Lunch: 3 phulkas/rice, dal/rajma/chole, sabzi, salad, curd.")
        lines.append("Pre-workout: Banana + coffee/tea.")
        lines.append("Post-workout: Protein (paneer/soya/whey) + fruit.")
        lines.append("Dinner: Similar to lunch, slightly lighter on carbs.")
        lines.append("Note: High protein (1.6–2.0 g/kg), 7–8 hrs sleep.")
    else:
        lines.append("Balanced Indian meals with dal, sabzi, roti/rice, curd in all main meals.")
        lines.append("Fruits + nuts for snacks, avoid junk/processed food.")

    lines.append("")
    lines.append("Disclaimer: General guidance only. For medical issues, consult a doctor/nutritionist.")

    return "\n".join(lines)


@app.route("/members/<int:member_id>/recommend", methods=["POST"])
@admin_required
def recommend(member_id):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    cur.execute("SELECT * FROM members WHERE member_id = %s", (member_id,))
    member = cur.fetchone()
    if not member:
        conn.close()
        flash("Member not found.", "danger")
        return redirect(url_for("members"))

    name = member["name"]
    goal = member.get("goal")
    height_cm = member.get("height_cm")

    cur.execute("""
        SELECT * FROM health_metrics
        WHERE member_id = %s
        ORDER BY record_date DESC, metric_id DESC
        LIMIT 1
    """, (member_id,))
    metrics = cur.fetchone()

    if not metrics:
        conn.close()
        flash("No health metrics found for this member.", "warning")
        return redirect(url_for("health_metrics", member_id=member_id))

    bmi = float(metrics["bmi"]) if metrics["bmi"] is not None else None
    weight_kg = float(metrics["weight_kg"]) if metrics["weight_kg"] is not None else None
    hr = int(metrics["resting_heart_rate"]) if metrics["resting_heart_rate"] is not None else None

    text = build_recommendation_text(name, goal, bmi, weight_kg, hr, height_cm)
    rec_date = date.today().isoformat()

    cur2 = conn.cursor()
    cur2.execute("""
        INSERT INTO workout_recommendations (member_id, recommendation_date, text)
        VALUES (%s, %s, %s)
    """, (member_id, rec_date, text))
    conn.commit()
    conn.close()

    flash("AI recommendation generated (workout + meal plan).", "success")
    return redirect(url_for("health_metrics", member_id=member_id))


@app.route("/recommendations")
@admin_required
def recommendations():
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT r.rec_id,
               r.member_id,
               r.recommendation_date,
               r.text,
               m.name AS member_name,
               m.goal
        FROM workout_recommendations r
        JOIN members m ON r.member_id = m.member_id
        ORDER BY r.recommendation_date DESC, r.rec_id DESC
    """)
    recs = cur.fetchall()
    conn.close()
    return render_template("recommendations.html", recs=recs)


# ---------- MAIN ----------

if __name__ == "__main__":
    app.run(debug=True)
