from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    Response,
)
import mysql.connector
from datetime import datetime, date
import re
from functools import wraps
import csv
import io
from werkzeug.security import check_password_hash

from ai_ollama import generate_ai_plan_text  # local AI (Ollama + Phi-3)


from twilio.rest import Client

import os

# Twilio Configuration (Use environment variables for security)
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "your_account_sid")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "your_auth_token")
TWILIO_PHONE = os.getenv("TWILIO_PHONE", "your_twilio_phone")

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


app = Flask(__name__)
app.secret_key = "supersecretkey"  # change for production

# ---------- DB CONFIG ----------
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "Kushal@1426",   # your MySQL password
    "database": "gymmaster",
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
            return redirect(url_for("trainer_dashboard"))  # ✅ FIX

        return f(*args, **kwargs)
    return wrapper



def trainer_or_admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("login"))

        if session.get("role") not in ("admin", "trainer"):
            flash("Access denied.", "danger")
            return redirect(url_for("login"))

        return f(*args, **kwargs)
    return wrapper


# ---------- VALIDATION HELPERS ----------

def is_valid_phone(phone: str) -> bool:
    # Indian mobile, 10 digits, starting 6–9
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

        flash(f"Welcome {user['username']}!", "success")

        if user["role"] == "admin":
          return redirect(url_for("dashboard"))
        elif user["role"] == "trainer":
          return redirect(url_for("trainer_dashboard"))
        else:
          flash("Invalid role configuration.", "danger")
          return redirect(url_for("login"))


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
        (selected_date,),
    )
    day_attendance = cur.fetchone()["c"]

    cur.execute(
        """
        SELECT IFNULL(SUM(amount), 0) AS total
        FROM payments
        WHERE status = 'paid' AND payment_date = %s
    """,
        (selected_date,),
    )
    day_revenue = float(cur.fetchone()["total"])

    # Recent activities
    cur.execute(
        """
        SELECT a.checkin_time, m.name
        FROM attendance a
        JOIN members m ON a.member_id = m.member_id
        ORDER BY a.checkin_time DESC
        LIMIT 5
    """
    )
    recent_attendance = cur.fetchall()

    cur.execute(
        """
        SELECT p.payment_date, p.amount, m.name AS member_name
        FROM payments p
        JOIN members m ON p.member_id = m.member_id
        ORDER BY p.payment_date DESC, p.payment_id DESC
        LIMIT 5
    """
    )
    recent_payments = cur.fetchall()

    # Members joined per month (last 6)
    cur.execute(
        """
        SELECT DATE_FORMAT(join_date, '%Y-%m') AS ym, COUNT(*) AS c
        FROM members
        GROUP BY ym
        ORDER BY ym
        LIMIT 6
    """
    )
    rows = cur.fetchall()
    member_month_labels = [r["ym"] for r in rows]
    member_month_values = [r["c"] for r in rows]

    # Revenue per month (last 6, paid only)
    cur.execute(
        """
        SELECT DATE_FORMAT(payment_date, '%Y-%m') AS ym, IFNULL(SUM(amount),0) AS total
        FROM payments
        WHERE status = 'paid'
        GROUP BY ym
        ORDER BY ym
        LIMIT 6
    """
    )
    rows = cur.fetchall()
    revenue_month_labels = [r["ym"] for r in rows]
    revenue_month_values = [float(r["total"]) for r in rows]

    # Attendance last 7 days
    cur.execute(
        """
        SELECT DATE(checkin_time) AS d, COUNT(*) AS c
        FROM attendance
        WHERE DATE(checkin_time) >= DATE_SUB(CURDATE(), INTERVAL 6 DAY)
        GROUP BY d
        ORDER BY d
    """
    )
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
        att_values=att_values,
    )


# ---------- MEMBERS ----------

@app.route("/trainer")
@trainer_or_admin_required
def trainer_dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "trainer":
        return redirect(url_for("trainer_dashboard"))


    return render_template("trainer_dashboard.html")

@app.route("/members")
@trainer_or_admin_required
def members():
    q = (request.args.get("q") or "").strip()
    plan = (request.args.get("plan") or "").strip()

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
                        END
                    ) < CURDATE()
                    THEN 'Expired'
                    WHEN (
                        CASE m.membership_plan
                            WHEN 'Monthly'   THEN DATE_ADD(m.join_date, INTERVAL 1 MONTH)
                            WHEN 'Quarterly' THEN DATE_ADD(m.join_date, INTERVAL 3 MONTH)
                            WHEN 'Yearly'    THEN DATE_ADD(m.join_date, INTERVAL 12 MONTH)
                        END
                    ) BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 7 DAY)
                    THEN 'Expiring Soon'
                    ELSE 'Active'
               END AS membership_status
        FROM members m
        WHERE 1=1
    """

    params = []

    # 🔍 Search filter
    if q:
        base_sql += " AND (m.name LIKE %s OR m.phone LIKE %s OR m.email LIKE %s)"
        like = f"%{q}%"
        params.extend([like, like, like])

    # 📅 Plan filter
    if plan in ("Monthly", "Quarterly", "Yearly"):
        base_sql += " AND m.membership_plan = %s"
        params.append(plan)

    base_sql += " ORDER BY m.join_date DESC"

    cur.execute(base_sql, params)
    members_rows = cur.fetchall()
    conn.close()

    return render_template(
        "members.html",
        members=members_rows,
        q=q,
        plan=plan
    )


@app.route("/members/add", methods=["GET", "POST"])
@admin_required
def add_member():
    # extended form with gender, age, current_weight_kg, diet_preference
    form = {
        "name": "",
        "gender": "",
        "age": "",
        "current_weight_kg": "",
        "phone": "",
        "email": "",
        "height_cm": "",
        "diet_preference": "",
        "membership_plan": "",
        "goal": "",
    }

    if request.method == "POST":
        form["name"] = (request.form.get("name") or "").strip()
        form["gender"] = (request.form.get("gender") or "").strip()
        form["age"] = (request.form.get("age") or "").strip()
        form["current_weight_kg"] = (request.form.get("current_weight_kg") or "").strip()
        form["phone"] = (request.form.get("phone") or "").strip()
        form["email"] = (request.form.get("email") or "").strip()
        form["height_cm"] = (request.form.get("height_cm") or "").strip()
        form["diet_preference"] = (request.form.get("diet_preference") or "").strip()
        form["membership_plan"] = (request.form.get("membership_plan") or "").strip()
        form["goal"] = (request.form.get("goal") or "").strip()

        errors = []

        # Name
        if not form["name"]:
            errors.append("Name is required.")

        # Gender
        if not form["gender"]:
          errors.append("Gender is required.")
        else:
          form["gender"] = form["gender"].lower()
          if form["gender"] not in ["male", "female", "other"]:
             errors.append("Invalid gender selected.")
    

        # Age
        if not form["age"]:
            errors.append("Age is required.")
        else:
            try:
                age_val = int(form["age"])
                if age_val <= 0 or age_val > 120:
                    errors.append("Age must be between 1 and 120.")
            except ValueError:
                errors.append("Age must be a valid integer.")

        # Current Weight
        if not form["current_weight_kg"]:
            errors.append("Current weight is required.")
        else:
            try:
                w = float(form["current_weight_kg"])
                if w <= 20 or w > 500:
                    errors.append("Weight must be between 20 and 500 kg.")
            except ValueError:
                errors.append("Weight must be a valid number.")

        # Phone
        if not form["phone"]:
            errors.append("Phone number is required.")
        elif not is_valid_phone(form["phone"]):
            errors.append(
                "Phone number must be a valid 10-digit Indian mobile number starting with 6–9."
            )

        # Email
        if not form["email"]:
            errors.append("Email address is required.")
        elif not is_valid_email(form["email"]):
            errors.append("Please enter a valid email address.")

        # Height
        if not form["height_cm"]:
            errors.append("Height (in cm) is required.")
        else:
            try:
                h = float(form["height_cm"])
                if h <= 0 or h > 300:
                    errors.append("Height must be a positive number less than 300 cm.")
            except ValueError:
                errors.append("Height must be a valid number.")

        # Diet preference
        if not form["diet_preference"]:
            errors.append("Diet preference is required.")
        elif form["diet_preference"] not in ["veg", "non_veg", "mixed"]:
            errors.append("Invalid diet preference selected.")

        # Membership + goal
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
        # insert including new columns
        cur.execute(
            """
            INSERT INTO members
                (name, gender, age, current_weight_kg, phone, email,
                 join_date, height_cm, diet_preference, membership_plan, goal)
            VALUES (%s,   %s,     %s,  %s,               %s,   %s,
                    %s,        %s,        %s,              %s,              %s)
        """,
            (
                form["name"],
                form["gender"],
                form["age"],
                form["current_weight_kg"],
                form["phone"],
                form["email"],
                join_date,
                form["height_cm"],
                form["diet_preference"],
                form["membership_plan"],
                form["goal"],
            ),
        )
        conn.commit()
        conn.close()
        flash("Member added successfully!", "success")
        return redirect(url_for("members"))

    return render_template("member_form.html", form=form)


@app.route("/members/plan/<plan>")
@trainer_or_admin_required
def members_by_plan(plan):
    if plan not in ("Monthly", "Quarterly", "Yearly"):
        flash("Invalid membership plan.", "danger")
        return redirect(url_for("members"))

    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    cur.execute("""
        SELECT m.*,
               (SELECT COUNT(*) FROM attendance a WHERE a.member_id = m.member_id) AS visit_count,
               (SELECT t.name FROM trainers t
                    JOIN member_trainer mt ON t.trainer_id = mt.trainer_id
                    WHERE mt.member_id = m.member_id
                    LIMIT 1) AS trainer_name
        FROM members m
        WHERE m.membership_plan = %s
        ORDER BY m.join_date DESC
    """, (plan,))

    members_rows = cur.fetchall()
    conn.close()

    return render_template(
        "members_by_plan.html",
        members=members_rows,
        plan=plan
    )



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
@app.route("/members/<int:member_id>/renew", methods=["GET", "POST"])
@admin_required
def renew_member(member_id):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    cur.execute("SELECT * FROM members WHERE member_id = %s", (member_id,))
    member = cur.fetchone()

    if not member:
        conn.close()
        flash("Member not found.", "danger")
        return redirect(url_for("members"))

    if request.method == "POST":
        new_plan = request.form.get("membership_plan")

        if new_plan not in ("Monthly", "Quarterly", "Yearly"):
            flash("Invalid plan selected.", "danger")
            conn.close()
            return redirect(url_for("renew_member", member_id=member_id))

        cur.execute(
            """
            UPDATE members
            SET membership_plan = %s,
                join_date = %s
            WHERE member_id = %s
            """,
            (new_plan, date.today().isoformat(), member_id),
        )

        conn.commit()
        conn.close()

        flash("Membership renewed successfully!", "success")
        return redirect(url_for("members"))

    conn.close()
    return render_template("renew_member.html", member=member)



# ---------- TRAINERS ----------

@app.route("/trainers")
@admin_required
def trainers():
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM trainers ORDER BY trainer_id DESC")
    trainers_rows = cur.fetchall()
    conn.close()
    return render_template("trainers.html", trainers=trainers_rows)


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
            errors.append(
                "Trainer phone must be a valid 10-digit Indian mobile number starting with 6–9."
            )

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("trainer_form.html", form=form)

        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO trainers (name, specialization, phone)
            VALUES (%s, %s, %s)
        """,
            (form["name"], form["specialization"] or None, form["phone"] or None),
        )
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
    members_list = cur.fetchall()
    cur.execute("SELECT trainer_id, name, specialization FROM trainers ORDER BY name")
    trainers_list = cur.fetchall()

    if request.method == "POST":
        member_id = request.form.get("member_id")
        trainer_id = request.form.get("trainer_id")
        assigned_date = date.today().isoformat()

        if not member_id or not trainer_id:
            flash("Please select both member and trainer.", "danger")
            conn.close()
            return render_template(
                "assign_trainer.html", members=members_list, trainers=trainers_list
            )

        cur2 = conn.cursor()
        cur2.execute(
            "SELECT member_id FROM member_trainer WHERE member_id = %s", (member_id,)
        )
        existing = cur2.fetchone()
        if existing:
            cur2.execute(
                """
                UPDATE member_trainer
                SET trainer_id = %s, assigned_date = %s
                WHERE member_id = %s
            """,
                (trainer_id, assigned_date, member_id),
            )
            flash("Trainer updated for member.", "info")
        else:
            cur2.execute(
                """
                INSERT INTO member_trainer (member_id, trainer_id, assigned_date)
                VALUES (%s, %s, %s)
            """,
                (member_id, trainer_id, assigned_date),
            )
            flash("Trainer assigned to member.", "success")

        conn.commit()
        conn.close()
        return redirect(url_for("assign_trainer"))

    conn.close()
    return render_template("assign_trainer.html", members=members_list, trainers=trainers_list)


# ---------- ATTENDANCE ----------

# ---------- ATTENDANCE ----------

@app.route("/attendance", methods=["GET", "POST"])
@trainer_or_admin_required
def attendance():
    selected_date = request.args.get("date") or date.today().isoformat()

    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    cur.execute(
        """
        SELECT a.checkin_time, a.attendance_date, m.member_id, m.name
        FROM attendance a
        JOIN members m ON a.member_id = m.member_id
        WHERE a.attendance_date = %s
        ORDER BY a.checkin_time DESC
        """,
        (selected_date,),
    )

    records = cur.fetchall()
    conn.close()

    return render_template(
        "attendance.html",
        records=records,
        selected_date=selected_date,
    )


@app.route("/attendance/mark", methods=["GET", "POST"])
@trainer_or_admin_required
def mark_attendance():
    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    cur.execute("SELECT member_id, name FROM members ORDER BY name")
    members_list = cur.fetchall()

    if request.method == "POST":
        member_id = request.form.get("member_id")
        today = date.today().isoformat()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not member_id:
            flash("Please select a member.", "danger")
            conn.close()
            return render_template("attendance_form.html", members=members_list)

        # 🔴 DUPLICATE CHECK
        cur.execute(
            """
            SELECT 1 FROM attendance
            WHERE member_id = %s AND attendance_date = %s
            """,
            (member_id, today),
        )

        if cur.fetchone():
            flash("Attendance already marked for this member today.", "warning")
            conn.close()
            return redirect(url_for("attendance"))

        # ✅ INSERT
        cur.execute(
            """
            INSERT INTO attendance (member_id, checkin_time, attendance_date)
            VALUES (%s, %s, %s)
            """,
            (member_id, now, today),
        )
        conn.commit()
        conn.close()

        flash("Attendance marked successfully.", "success")
        return redirect(url_for("attendance"))

    conn.close()
    return render_template("attendance_form.html", members=members_list)


@app.route("/attendance/history")
@trainer_or_admin_required
def attendance_history():
    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    cur.execute(
        """
        SELECT attendance_date, COUNT(*) AS total_present
        FROM attendance
        GROUP BY attendance_date
        ORDER BY attendance_date DESC
        """
    )

    days = cur.fetchall()
    conn.close()

    return render_template("attendance_history.html", days=days)


@app.route("/attendance/export")
@trainer_or_admin_required
def export_attendance():
    selected_date = request.args.get("date") or date.today().isoformat()

    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    cur.execute(
        """
        SELECT a.checkin_time, m.member_id, m.name
        FROM attendance a
        JOIN members m ON a.member_id = m.member_id
        WHERE a.attendance_date = %s
        ORDER BY a.checkin_time
        """,
        (selected_date,),
    )

    rows = cur.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["Date", "Check-in Time", "Member ID", "Member Name"])

    for r in rows:
        dt = r["checkin_time"]

        # ✅ FORCE EXCEL TEXT (FINAL FIX)
        date_str = "'" + dt.strftime("%Y-%m-%d")
        time_str = "'" + dt.strftime("%H:%M:%S")

        writer.writerow([
            date_str,
            time_str,
            r["member_id"],
            r["name"],
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment;filename=attendance_{selected_date}.csv"
        },
    )




# ---------- PAYMENTS ----------

@app.route("/payments")
@admin_required
def payments():
    status_filter = request.args.get("status")  # paid / pending / None

    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    if status_filter in ("paid", "pending"):
        cur.execute(
            """
            SELECT p.payment_id, p.member_id, p.amount, p.payment_date,
                   p.payment_mode, p.status,
                   m.name AS member_name
            FROM payments p
            JOIN members m ON p.member_id = m.member_id
            WHERE p.status = %s
            ORDER BY p.payment_date DESC, p.payment_id DESC
            """,
            (status_filter,),
        )
    else:
        cur.execute(
            """
            SELECT p.payment_id, p.member_id, p.amount, p.payment_date,
                   p.payment_mode, p.status,
                   m.name AS member_name
            FROM payments p
            JOIN members m ON p.member_id = m.member_id
            ORDER BY p.payment_date DESC, p.payment_id DESC
            """
        )

    rows = cur.fetchall()
    conn.close()

    return render_template(
        "payments.html",
        payments=rows,
        status_filter=status_filter
    )



@app.route("/payments/add", methods=["GET", "POST"])
@admin_required
def add_payment():
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT member_id, name FROM members ORDER BY name")
    members_list = cur.fetchall()

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
            return render_template("payment_form.html", members=members_list)

        payment_date = date.today().isoformat()
        conn2 = get_connection()
        cur2 = conn2.cursor()
        cur2.execute(
            """
            INSERT INTO payments (member_id, amount, payment_date, payment_mode, status)
            VALUES (%s, %s, %s, %s, %s)
        """,
            (member_id, amount_raw, payment_date, mode, status),
        )
        conn2.commit()
        conn2.close()

        flash("Payment recorded successfully.", "success")
        return redirect(url_for("payments"))

    conn.close()
    return render_template("payment_form.html", members=members_list)


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
@trainer_or_admin_required
def health_metrics(member_id):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    cur.execute("SELECT * FROM members WHERE member_id = %s", (member_id,))
    member = cur.fetchone()
    if not member:
        conn.close()
        flash("Member not found.", "danger")
        return redirect(url_for("members"))

    cur.execute(
        """
        SELECT t.name FROM trainers t
        JOIN member_trainer mt ON t.trainer_id = mt.trainer_id
        WHERE mt.member_id = %s
    """,
        (member_id,),
    )
    trainer_row = cur.fetchone()
    trainer = trainer_row["name"] if trainer_row else None

    cur.execute(
        """
        SELECT * FROM health_metrics
        WHERE member_id = %s
        ORDER BY record_date DESC, metric_id DESC
    """,
        (member_id,),
    )
    metrics = cur.fetchall()

    summary = None
    if metrics:
        latest = metrics[0]
        earliest = metrics[-1]
        latest_bmi = latest["bmi"]
        latest_weight = latest["weight_kg"]
        weight_change = None
        if latest_weight is not None and earliest["weight_kg"] is not None:
            weight_change = round(
                float(latest_weight) - float(earliest["weight_kg"]), 1
            )

        category = bmi_category(latest_bmi)
        progress_text = "Not enough data yet."

        if weight_change is not None and member.get("goal"):
            goal = member["goal"]
            if goal == "weight_loss":
                if weight_change < -1:
                    progress_text = f"Good progress: {abs(weight_change)} kg lost."
                elif weight_change > 1:
                    progress_text = (
                        f"Moving away from goal: {weight_change} kg gained."
                    )
                else:
                    progress_text = "Weight is relatively stable."
            elif goal == "muscle_gain":
                if weight_change > 1:
                    progress_text = f"Good progress: {weight_change} kg gained."
                elif weight_change < -1:
                    progress_text = (
                        f"Moving away from goal: {abs(weight_change)} kg lost."
                    )
                else:
                    progress_text = "Weight is relatively stable."
            else:
                progress_text = "Goal: general fitness; keep consistency."

        cur.execute(
            """
            SELECT COUNT(*) AS visits,
                   MAX(checkin_time) AS last_visit
            FROM attendance
            WHERE member_id = %s
        """,
            (member_id,),
        )
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

    cur.execute(
        """
        SELECT * FROM workout_recommendations
        WHERE member_id = %s
        ORDER BY recommendation_date DESC, rec_id DESC
        LIMIT 5
    """,
        (member_id,),
    )
    recs = cur.fetchall()

    conn.close()
    return render_template(
        "health_metrics.html",
        member=member,
        metrics=metrics,
        recs=recs,
        summary=summary,
        trainer=trainer,
    )


@app.route("/members/<int:member_id>/health/add", methods=["GET", "POST"])
@trainer_or_admin_required
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
        cur2.execute(
            """
            INSERT INTO health_metrics (member_id, record_date, weight_kg, bmi, resting_heart_rate)
            VALUES (%s, %s, %s, %s, %s)
        """,
            (member_id, record_date, weight_kg, bmi_val, hr_val),
        )
        conn.commit()
        conn.close()
        flash("Health metrics recorded.", "success")
        return redirect(url_for("health_metrics", member_id=member_id))

    conn.close()
    return render_template("health_form.html", member=member)


# ---------- OLD RULE-BASED AI RECOMMENDATIONS (LIST PAGE) ----------



# ---------- NEW: LOCAL AI WEEKLY PLAN + CALORIE TRACKER (OLLAMA + PHI-3) ----------

@app.route("/members/<int:member_id>/ai-plan", methods=["GET", "POST"])
@trainer_or_admin_required
def ai_plan(member_id):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    # 1. Load member
    cur.execute("SELECT * FROM members WHERE member_id = %s", (member_id,))
    member = cur.fetchone()
    if not member:
        conn.close()
        flash("Member not found.", "danger")
        return redirect(url_for("members"))

    # 2. Latest health metrics
    cur.execute(
        """
        SELECT * FROM health_metrics
        WHERE member_id = %s
        ORDER BY record_date DESC, metric_id DESC
        LIMIT 1
    """,
        (member_id,),
    )
    latest_metric = cur.fetchone()

    # 3. Handle actions
    action = request.form.get("action") if request.method == "POST" else None

    if request.method == "POST":
        # Generate / refresh AI plan
        if action == "generate_plan":
            try:
                calories, macros, plan_text = generate_ai_plan_text(
                    member, latest_metric
                )
            except Exception as e:
                conn.close()
                flash(f"AI error (Ollama): {e}", "danger")
                return redirect(url_for("ai_plan", member_id=member_id))

            protein_g = macros["protein"]
            carbs_g = macros["carbs"]
            fats_g = macros["fat"]

            cur2 = conn.cursor()
            cur2.execute(
                """
                INSERT INTO ai_plans (member_id, calories, protein_g, carbs_g, fats_g, model_used, plan_text)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
                (member_id, calories, protein_g, carbs_g, fats_g, "ollama-phi3", plan_text),
            )
            conn.commit()
            flash("AI weekly plan generated successfully.", "success")
            return redirect(url_for("ai_plan", member_id=member_id))

        # Add food entry
        elif action == "add_food":
            food_name = request.form.get("food_name")
            calories_val = request.form.get("calories") or 0
            protein_val = request.form.get("protein") or 0
            carbs_val = request.form.get("carbs") or 0
            fats_val = request.form.get("fats") or 0

            today = date.today().isoformat()
            cur2 = conn.cursor()
            cur2.execute(
                """
                INSERT INTO food_log (member_id, log_date, food_name, calories, protein_g, carbs_g, fats_g)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
                (
                    member_id,
                    today,
                    food_name,
                    calories_val,
                    protein_val,
                    carbs_val,
                    fats_val,
                ),
            )
            conn.commit()
            flash("Food entry added.", "success")
            return redirect(url_for("ai_plan", member_id=member_id))

        # Add workout entry
        elif action == "add_workout":
            workout_name = request.form.get("workout_name")
            duration = request.form.get("duration") or 0
            burned = request.form.get("burned_calories") or 0

            today = date.today().isoformat()
            cur2 = conn.cursor()
            cur2.execute(
                """
                INSERT INTO workout_log (member_id, log_date, workout_name, duration_min, burned_calories)
                VALUES (%s, %s, %s, %s, %s)
            """,
                (member_id, today, workout_name, duration, burned),
            )
            conn.commit()
            flash("Workout entry added.", "success")
            return redirect(url_for("ai_plan", member_id=member_id))

    # 4. Load last generated AI plan
    cur.execute(
        """
        SELECT * FROM ai_plans
        WHERE member_id = %s
        ORDER BY created_at DESC, plan_id DESC
        LIMIT 1
    """,
        (member_id,),
    )
    ai_plan_row = cur.fetchone()

    # 5. Load today's logs
    today = date.today().isoformat()

    cur.execute(
        """
        SELECT * FROM food_log
        WHERE member_id = %s AND log_date = %s
        ORDER BY log_id DESC
    """,
        (member_id, today),
    )
    food_entries = cur.fetchall()

    cur.execute(
        """
        SELECT * FROM workout_log
        WHERE member_id = %s AND log_date = %s
        ORDER BY log_id DESC
    """,
        (member_id, today),
    )
    workout_entries = cur.fetchall()

    cur.execute(
        """
        SELECT 
            IFNULL(SUM(calories), 0) AS total_cal,
            IFNULL(SUM(protein_g), 0) AS total_protein,
            IFNULL(SUM(carbs_g), 0) AS total_carbs,
            IFNULL(SUM(fats_g), 0) AS total_fats
        FROM food_log
        WHERE member_id = %s AND log_date = %s
    """,
        (member_id, today),
    )
    food_totals = cur.fetchone()

    cur.execute(
        """
        SELECT IFNULL(SUM(burned_calories), 0) AS total_burned
        FROM workout_log
        WHERE member_id = %s AND log_date = %s
    """,
        (member_id, today),
    )
    burned_total = cur.fetchone()

    conn.close()

    target_cal = (
        ai_plan_row["calories"] if ai_plan_row and ai_plan_row.get("calories") else None
    )

    return render_template(
        "ai_plan.html",
        member=member,
        latest_metric=latest_metric,
        ai_plan=ai_plan_row,
        food_entries=food_entries,
        workout_entries=workout_entries,
        food_totals=food_totals,
        burned_total=burned_total,
        target_calories=target_cal,
        today=today,
    )


def send_sms(to_phone, message):
    try:
        if not to_phone:
            raise ValueError("Phone number missing")

        phone = to_phone.strip()

        # India support
        if not phone.startswith("+"):
            phone = "+91" + phone

        msg = twilio_client.messages.create(
            body=message,
            from_=TWILIO_PHONE,
            to=phone
        )

        print("SMS SENT:", msg.sid)
        return True

    except Exception as e:
        print("❌ SMS ERROR:", e)
        return False


@app.route("/members/<int:member_id>/send-health-sms", methods=["POST"])
@trainer_or_admin_required
def send_health_sms(member_id):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    cur.execute("SELECT name, phone FROM members WHERE member_id=%s", (member_id,))
    member = cur.fetchone()

    cur.execute("""
        SELECT weight_kg, bmi, resting_heart_rate
        FROM health_metrics
        WHERE member_id=%s
        ORDER BY record_date DESC
        LIMIT 1
    """, (member_id,))
    health = cur.fetchone()

    conn.close()

    if not member or not health:
        flash("No health data available.", "warning")
        return redirect(url_for("health_metrics", member_id=member_id))

    message = (
        f"🏋️ GymMaster Health Update\n\n"
        f"Member: {member['name']}\n"
        f"Weight: {health['weight_kg']} kg\n"
        f"BMI: {health['bmi']}\n"
        f"Heart Rate: {health['resting_heart_rate']} bpm\n\n"
        f"Stay consistent 💪"
    )

    if send_sms(member["phone"], message):
        flash("Health SMS sent successfully.", "success")
    else:
        flash("Failed to send Health SMS. Check console.", "danger")

    return redirect(url_for("health_metrics", member_id=member_id))

@app.route("/members/<int:member_id>/send-ai-sms", methods=["POST"])
@trainer_or_admin_required
def send_ai_sms(member_id):
    conn = get_connection()
    cur = conn.cursor(dictionary=True)

    cur.execute("SELECT name, phone FROM members WHERE member_id=%s", (member_id,))
    member = cur.fetchone()

    cur.execute("""
        SELECT calories, protein_g, carbs_g, fats_g
        FROM ai_plans
        WHERE member_id=%s
        ORDER BY created_at DESC
        LIMIT 1
    """, (member_id,))
    plan = cur.fetchone()

    conn.close()

    if not member or not plan:
        flash("AI plan not found.", "warning")
        return redirect(url_for("health_metrics", member_id=member_id))

    message = (
        f"🧠 GymMaster AI Plan\n\n"
        f"Member: {member['name']}\n"
        f"Calories: {plan['calories']} kcal\n"
        f"Protein: {plan['protein_g']} g\n"
        f"Carbs: {plan['carbs_g']} g\n"
        f"Fats: {plan['fats_g']} g\n\n"
        f"Train smart 💪"
    )

    if send_sms(member["phone"], message):
        flash("AI Plan SMS sent successfully.", "success")
    else:
        flash("Failed to send AI Plan SMS.", "danger")

    return redirect(url_for("ai_plan", member_id=member_id))





# ---------- MAIN ----------

if __name__ == "__main__":
    app.run(debug=True)