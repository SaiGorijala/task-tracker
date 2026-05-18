import os
import smtplib
import secrets
from email.message import EmailMessage
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
UPLOAD_DIR = BASE_DIR / "uploads"
PROFILE_PHOTO_DIR = UPLOAD_DIR / "profile_photos"
GDRIVE_DIR = BASE_DIR / "gdrive"

db = SQLAlchemy()


FUNCTIONS = ["Procurement", "Facilities", "IT Infrastructure", "HR"]
HR_SUB_FUNCTIONS = ["Talent Acquisition", "HR Operations", "L&D", "BU HR"]
PERIODICITY_CHOICES = ["One-time", "Monthly", "Quarterly", "Half-Yearly", "Yearly"]
TASK_STATUSES = ["Open", "Submitted", "Approved", "Rejected", "Closed", "Overdue"]


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")
    function_name = db.Column(db.String(80))
    reporting_manager_email = db.Column(db.String(120))
    function_head_email = db.Column(db.String(120))

    tasks = db.relationship(
        "Task",
        foreign_keys="Task.responsible_user_id",
        backref="responsible_user",
        lazy=True,
    )
    created_tasks = db.relationship(
        "Task",
        foreign_keys="Task.created_by_id",
        backref="created_by",
        lazy=True,
    )

    profile = db.relationship("UserProfile", backref="user", uselist=False, cascade="all, delete-orphan")
    security = db.relationship("UserSecurity", backref="user", uselist=False, cascade="all, delete-orphan")


class UserProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    photo_filename = db.Column(db.String(255))
    dob = db.Column(db.Date)
    phone = db.Column(db.String(40))


class UserSecurity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)
    temp_password_created_at = db.Column(db.DateTime)


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    function_name = db.Column(db.String(80), nullable=False)
    sub_function = db.Column(db.String(80))
    topic = db.Column(db.String(255), nullable=False)
    target_date = db.Column(db.Date, nullable=False)
    periodicity = db.Column(db.String(40), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Open")
    rejection_comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    responsible_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    source_task_id = db.Column(db.Integer, db.ForeignKey("task.id"))
    escalation_level_sent = db.Column(db.Integer, default=0, nullable=False)

    submissions = db.relationship("Submission", backref="task", lazy=True, cascade="all, delete-orphan")


class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("task.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)
    # Kept as "sharepoint_path" column name for backward compatibility; now points to Google Drive-style storage path.
    sharepoint_path = db.Column(db.String(500), nullable=False)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", backref="submissions")


class EscalationConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True, default=1)
    reminder_days_1 = db.Column(db.Integer, default=3, nullable=False)
    reminder_days_2 = db.Column(db.Integer, default=1, nullable=False)
    level_2_days = db.Column(db.Integer, default=3, nullable=False)
    level_3_days = db.Column(db.Integer, default=7, nullable=False)
    include_reporting_manager_l2 = db.Column(db.Boolean, default=True, nullable=False)
    include_function_head_l3 = db.Column(db.Boolean, default=True, nullable=False)
    extra_recipients = db.Column(db.String(500), default="")


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event = db.Column(db.String(120), nullable=False)
    details = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class EmailSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True, default=1)
    automated_emails_enabled = db.Column(db.Boolean, default=False, nullable=False)
    ai_composer_enabled = db.Column(db.Boolean, default=True, nullable=False)
    from_email = db.Column(db.String(120), default="")
    signature = db.Column(db.String(500), default="— Task Tracking Portal")


class OutboxEmail(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(80), nullable=False)
    to_email = db.Column(db.String(120), nullable=False)
    cc_emails = db.Column(db.String(500), default="")
    subject = db.Column(db.String(250), nullable=False)
    body = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Pending")  # Pending/Sent/Failed
    last_error = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    sent_at = db.Column(db.DateTime)

    task_id = db.Column(db.Integer, db.ForeignKey("task.id"))
    submission_id = db.Column(db.Integer, db.ForeignKey("submission.id"))

    task = db.relationship("Task")
    submission = db.relationship("Submission")


def create_app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{BASE_DIR / 'task_tracker.db'}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key")
    app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
    app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)

    db.init_app(app)
    UPLOAD_DIR.mkdir(exist_ok=True, parents=True)
    PROFILE_PHOTO_DIR.mkdir(exist_ok=True, parents=True)
    GDRIVE_DIR.mkdir(exist_ok=True, parents=True)

    with app.app_context():
        db.create_all()
        ensure_seed_data()
        ensure_gdrive_structure()

    @app.template_filter("datefmt")
    def datefmt(value, fmt="%d-%b-%Y"):
        if not value:
            return "-"
        return value.strftime(fmt)

    @app.context_processor
    def inject_globals():
        current = current_user()
        return {"current_user": current, "TASK_STATUSES": TASK_STATUSES}

    @app.errorhandler(PermissionError)
    def handle_permission_error(_error):
        if not current_user():
            flash("Please login to continue.", "warning")
            return redirect(url_for("login"))
        flash("You do not have permission to access this page.", "danger")
        if is_admin():
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("my_tasks"))

    @app.route("/")
    def index():
        if not current_user():
            return redirect(url_for("login"))
        if is_admin():
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("my_tasks"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = User.query.filter_by(email=email).first()
            if not user or not check_password_hash(user.password_hash, password):
                flash("Invalid credentials.", "danger")
                return redirect(url_for("login"))
            session["user_id"] = user.id
            log_event("login", f"User {user.email} logged in.")
            if user.security and user.security.must_change_password:
                return redirect(url_for("change_password"))
            return redirect(url_for("index"))
        return render_template("login.html")

    @app.route("/change-password", methods=["GET", "POST"])
    def change_password():
        require_login()
        user = current_user()
        if request.method == "POST":
            new_pw = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")
            if len(new_pw) < 8:
                flash("Password must be at least 8 characters.", "warning")
                return redirect(url_for("change_password"))
            if new_pw != confirm:
                flash("Passwords do not match.", "danger")
                return redirect(url_for("change_password"))
            user.password_hash = generate_password_hash(new_pw)
            if user.security:
                user.security.must_change_password = False
            db.session.commit()
            log_event("password_changed", f"User {user.email} changed password.")
            flash("Password updated.", "success")
            return redirect(url_for("index"))
        return render_template("change_password.html")

    @app.route("/logout")
    def logout():
        user = current_user()
        if user:
            log_event("logout", f"User {user.email} logged out.")
        session.clear()
        return redirect(url_for("login"))

    @app.route("/admin/dashboard")
    def admin_dashboard():
        require_admin()
        run_escalation_engine()
        stats = (
            db.session.query(Task.status, func.count(Task.id))
            .group_by(Task.status)
            .all()
        )
        stats_map = {status: count for status, count in stats}
        escalated_count = EscalationLog.query.filter(EscalationLog.level > 0).count()
        return render_template(
            "admin_dashboard.html",
            stats=stats_map,
            escalated_count=escalated_count,
        )

    @app.route("/admin/tasks")
    def admin_tasks():
        require_admin()
        run_escalation_engine()
        tasks = Task.query.order_by(Task.target_date.asc()).all()
        return render_template(
            "admin_tasks.html",
            tasks=tasks,
            users=User.query.filter(User.role == "user").all(),
            functions=FUNCTIONS,
            hr_sub_functions=HR_SUB_FUNCTIONS,
            periodicities=PERIODICITY_CHOICES,
        )

    @app.route("/admin/tasks/create", methods=["POST"])
    def create_task():
        require_admin()
        form = request.form
        function_name = form.get("function_name")
        sub_function = form.get("sub_function") if function_name == "HR" else None
        responsible_user_id = int(form.get("responsible_user_id"))
        target_date = datetime.strptime(form.get("target_date"), "%Y-%m-%d").date()
        task = Task(
            function_name=function_name,
            sub_function=sub_function,
            topic=form.get("topic"),
            target_date=target_date,
            periodicity=form.get("periodicity"),
            responsible_user_id=responsible_user_id,
            created_by_id=current_user().id,
            status="Open",
        )
        db.session.add(task)
        db.session.commit()
        send_task_created_email(task)
        log_event("task_created", f"Task {task.id} created for {task.responsible_user.email}")
        flash("Task created and notification sent.", "success")
        return redirect(url_for("admin_tasks"))

    @app.route("/admin/tasks/<int:task_id>/edit", methods=["GET", "POST"])
    def edit_task(task_id):
        require_admin()
        task = Task.query.get_or_404(task_id)
        if request.method == "POST":
            form = request.form
            task.function_name = form.get("function_name")
            task.sub_function = form.get("sub_function") if task.function_name == "HR" else None
            task.topic = form.get("topic")
            task.responsible_user_id = int(form.get("responsible_user_id"))
            task.target_date = datetime.strptime(form.get("target_date"), "%Y-%m-%d").date()
            task.periodicity = form.get("periodicity")
            db.session.commit()
            log_event("task_updated", f"Task {task.id} updated by admin.")
            flash("Task updated.", "success")
            return redirect(url_for("admin_tasks"))
        return render_template(
            "edit_task.html",
            task=task,
            users=User.query.filter(User.role == "user").all(),
            functions=FUNCTIONS,
            hr_sub_functions=HR_SUB_FUNCTIONS,
            periodicities=PERIODICITY_CHOICES,
        )

    @app.route("/admin/tasks/<int:task_id>/delete", methods=["POST"])
    def delete_task(task_id):
        require_admin()
        task = Task.query.get_or_404(task_id)
        db.session.delete(task)
        db.session.commit()
        log_event("task_deleted", f"Task {task.id} deleted.")
        flash("Task deleted.", "info")
        return redirect(url_for("admin_tasks"))

    @app.route("/user/tasks")
    def my_tasks():
        require_login()
        if is_admin():
            return redirect(url_for("admin_dashboard"))
        run_escalation_engine()
        user = current_user()
        tasks = Task.query.filter_by(responsible_user_id=user.id).order_by(Task.target_date.asc()).all()
        return render_template("user_dashboard.html", tasks=tasks)

    @app.route("/user/tasks/<int:task_id>/submit", methods=["POST"])
    def submit_task(task_id):
        require_login()
        task = Task.query.get_or_404(task_id)
        user = current_user()
        if task.responsible_user_id != user.id:
            flash("Unauthorized access.", "danger")
            return redirect(url_for("my_tasks"))
        uploaded = request.files.get("evidence_file")
        if not uploaded or not uploaded.filename:
            flash("Please choose a file.", "warning")
            return redirect(url_for("my_tasks"))
        filename = secure_filename(uploaded.filename)
        stamped_name = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{filename}"
        local_path = UPLOAD_DIR / stamped_name
        uploaded.save(local_path)
        drive_target = build_gdrive_path(task, datetime.utcnow())
        drive_target.mkdir(parents=True, exist_ok=True)
        moved_file = drive_target / stamped_name
        local_path.replace(moved_file)
        submission = Submission(
            task_id=task.id,
            user_id=user.id,
            original_filename=filename,
            stored_filename=stamped_name,
            sharepoint_path=str(moved_file),
        )
        task.status = "Submitted"
        task.escalation_level_sent = 0
        db.session.add(submission)
        db.session.commit()
        send_submission_email_to_admin(task, submission)
        log_event("task_submitted", f"Task {task.id} submitted by {user.email}.")
        flash("Evidence submitted successfully.", "success")
        return redirect(url_for("my_tasks"))

    @app.route("/uploads/<path:filename>")
    def uploaded_file(filename):
        require_admin()
        return send_from_directory(str(GDRIVE_DIR), filename, as_attachment=True)

    @app.route("/admin/tasks/<int:task_id>/review", methods=["POST"])
    def review_task(task_id):
        require_admin()
        task = Task.query.get_or_404(task_id)
        action = request.form.get("action")
        comment = request.form.get("comment", "").strip()
        if action == "approve":
            task.status = "Closed"
            task.rejection_comment = None
            send_approval_email(task)
            log_event("task_approved", f"Task {task.id} approved and closed.")
            if task.periodicity != "One-time":
                regenerate_task(task)
        elif action == "reject":
            task.status = "Rejected"
            task.rejection_comment = comment or "Please resubmit with correct evidence."
            send_rejection_email(task, task.rejection_comment)
            log_event("task_rejected", f"Task {task.id} rejected with comments.")
        db.session.commit()
        flash("Review action completed.", "success")
        return redirect(url_for("admin_tasks"))

    @app.route("/admin/escalation", methods=["GET", "POST"])
    def escalation_settings():
        require_admin()
        config = EscalationConfig.query.get(1)
        if request.method == "POST":
            config.reminder_days_1 = int(request.form.get("reminder_days_1", 3))
            config.reminder_days_2 = int(request.form.get("reminder_days_2", 1))
            config.level_2_days = int(request.form.get("level_2_days", 3))
            config.level_3_days = int(request.form.get("level_3_days", 7))
            config.include_reporting_manager_l2 = bool(request.form.get("include_reporting_manager_l2"))
            config.include_function_head_l3 = bool(request.form.get("include_function_head_l3"))
            config.extra_recipients = request.form.get("extra_recipients", "")
            db.session.commit()
            log_event("escalation_updated", "Escalation settings updated by admin.")
            flash("Escalation settings saved.", "success")
            return redirect(url_for("escalation_settings"))
        return render_template("escalation_settings.html", config=config)

    @app.route("/admin/users")
    def admin_users():
        require_admin()
        users = User.query.order_by(User.role.asc(), User.name.asc()).all()
        return render_template("admin_users.html", users=users, functions=FUNCTIONS)

    @app.route("/admin/users/create", methods=["POST"])
    def admin_create_user():
        require_admin()
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        role = request.form.get("role", "user").strip()
        phone = request.form.get("phone", "").strip()
        dob_raw = request.form.get("dob", "").strip()
        reporting_manager_email = request.form.get("reporting_manager_email", "").strip()
        function_head_email = request.form.get("function_head_email", "").strip()
        function_name = request.form.get("function_name", "").strip() or None

        if not name or not email:
            flash("Name and email are required.", "warning")
            return redirect(url_for("admin_users"))
        if User.query.filter_by(email=email).first():
            flash("User with this email already exists.", "danger")
            return redirect(url_for("admin_users"))

        temp_password = generate_temp_password()
        user = User(
            name=name,
            email=email,
            password_hash=generate_password_hash(temp_password),
            role=role,
            function_name=function_name,
            reporting_manager_email=reporting_manager_email or None,
            function_head_email=function_head_email or None,
        )
        db.session.add(user)
        db.session.commit()

        profile = UserProfile(user_id=user.id)
        if phone:
            profile.phone = phone
        if dob_raw:
            try:
                profile.dob = datetime.strptime(dob_raw, "%Y-%m-%d").date()
            except ValueError:
                pass
        db.session.add(profile)

        sec = UserSecurity(user_id=user.id, must_change_password=True, temp_password_created_at=datetime.utcnow())
        db.session.add(sec)

        photo = request.files.get("photo")
        if photo and photo.filename:
            fn = secure_filename(photo.filename)
            stamped = f"{user.id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{fn}"
            photo.save(PROFILE_PHOTO_DIR / stamped)
            profile.photo_filename = stamped

        db.session.commit()

        subject, body = compose_user_welcome_email(user, temp_password)
        queue_or_send_email("user_created", user.email, subject, body)
        log_event("user_created", f"Admin created user {user.email}")
        flash("User created. Login details queued/sent based on Email Settings.", "success")
        return redirect(url_for("admin_users"))

    @app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
    def admin_delete_user(user_id):
        require_admin()
        user = User.query.get_or_404(user_id)
        if user.role == "admin":
            flash("Admin user cannot be deleted.", "danger")
            return redirect(url_for("admin_users"))

        Task.query.filter_by(responsible_user_id=user.id).delete(synchronize_session=False)
        Submission.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        UserProfile.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        UserSecurity.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        db.session.delete(user)
        db.session.commit()
        log_event("user_deleted", f"Admin deleted user {user.email}")
        flash("User deleted.", "info")
        return redirect(url_for("admin_users"))

    @app.route("/admin/users/purge", methods=["POST"])
    def admin_purge_users():
        require_admin()
        users = User.query.filter(User.role != "admin").all()
        deleted = 0
        for u in users:
            Task.query.filter_by(responsible_user_id=u.id).delete(synchronize_session=False)
            Submission.query.filter_by(user_id=u.id).delete(synchronize_session=False)
            UserProfile.query.filter_by(user_id=u.id).delete(synchronize_session=False)
            UserSecurity.query.filter_by(user_id=u.id).delete(synchronize_session=False)
            db.session.delete(u)
            deleted += 1
        db.session.commit()
        log_event("users_purged", f"Admin purged {deleted} non-admin users.")
        flash(f"Deleted {deleted} users (non-admin).", "info")
        return redirect(url_for("admin_users"))

    @app.route("/profile-photos/<path:filename>")
    def profile_photo(filename):
        require_admin()
        return send_from_directory(str(PROFILE_PHOTO_DIR), filename)

    @app.route("/admin/email-settings", methods=["GET", "POST"])
    def email_settings():
        require_admin()
        settings = EmailSettings.query.get(1)
        if request.method == "POST":
            settings.automated_emails_enabled = bool(request.form.get("automated_emails_enabled"))
            settings.ai_composer_enabled = bool(request.form.get("ai_composer_enabled"))
            settings.from_email = request.form.get("from_email", "").strip()
            settings.signature = request.form.get("signature", "").strip() or settings.signature
            db.session.commit()
            log_event("email_settings_updated", "Email settings updated by admin.")
            flash("Email settings saved.", "success")
            return redirect(url_for("email_settings"))
        pending = OutboxEmail.query.filter_by(status="Pending").count()
        return render_template("email_settings.html", settings=settings, pending=pending)

    @app.route("/admin/email-center")
    def email_center():
        require_admin()
        pending = OutboxEmail.query.filter_by(status="Pending").order_by(OutboxEmail.created_at.desc()).limit(200).all()
        sent = OutboxEmail.query.filter_by(status="Sent").order_by(OutboxEmail.sent_at.desc()).limit(50).all()
        failed = OutboxEmail.query.filter_by(status="Failed").order_by(OutboxEmail.created_at.desc()).limit(50).all()
        return render_template("email_center.html", pending=pending, sent=sent, failed=failed)

    @app.route("/admin/email-center/<int:email_id>/send", methods=["POST"])
    def send_outbox_email(email_id):
        require_admin()
        item = OutboxEmail.query.get_or_404(email_id)
        item.subject = request.form.get("subject", "").strip() or item.subject
        item.body = request.form.get("body", "").strip() or item.body
        item.cc_emails = request.form.get("cc_emails", "").strip()
        ok, err = deliver_email(item.subject, [item.to_email], parse_cc(item.cc_emails), item.body)
        if ok:
            item.status = "Sent"
            item.sent_at = datetime.utcnow()
            item.last_error = None
            db.session.commit()
            log_event("email_sent", f"OutboxEmail {item.id} sent to {item.to_email}")
            flash("Email sent.", "success")
        else:
            item.status = "Failed"
            item.last_error = err
            db.session.commit()
            log_event("email_failed", f"OutboxEmail {item.id} failed: {err}")
            flash(f"Email failed: {err}", "danger")
        return redirect(url_for("email_center"))

    @app.route("/admin/email-center/motivate", methods=["POST"])
    def send_motivation_batch():
        require_admin()
        settings = EmailSettings.query.get(1)
        users = User.query.filter_by(role="user").all()
        created = 0
        sent = 0
        for u in users:
            subject, body = compose_motivation_email(u)
            if settings.automated_emails_enabled:
                ok, err = deliver_email(subject, [u.email], [], body)
                if ok:
                    sent += 1
                else:
                    db.session.add(
                        OutboxEmail(
                            event_type="motivation",
                            to_email=u.email,
                            subject=subject,
                            body=body + f"\n\n(Delivery error: {err})",
                            status="Failed",
                            last_error=err,
                        )
                    )
            else:
                db.session.add(
                    OutboxEmail(
                        event_type="motivation",
                        to_email=u.email,
                        subject=subject,
                        body=body,
                        status="Pending",
                    )
                )
                created += 1
        db.session.commit()
        if settings.automated_emails_enabled:
            flash(f"Motivation emails sent: {sent}", "success")
        else:
            flash(f"Motivation emails queued for review: {created}", "info")
        return redirect(url_for("email_center"))

    @app.route("/admin/audit-log")
    def audit_log():
        require_admin()
        logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(200).all()
        escalations = EscalationLog.query.order_by(EscalationLog.triggered_at.desc()).limit(200).all()
        return render_template("audit_log.html", logs=logs, escalations=escalations)

    return app


class EscalationLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("task.id"), nullable=False)
    level = db.Column(db.Integer, nullable=False, default=0)
    recipients = db.Column(db.String(500), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    triggered_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    task = db.relationship("Task", backref="escalations")


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return User.query.get(uid)


def require_login():
    if not current_user():
        raise PermissionError("Not logged in")


def is_admin():
    user = current_user()
    return bool(user and user.role == "admin")


def require_admin():
    if not current_user():
        raise PermissionError("Not logged in")
    if not is_admin():
        raise PermissionError("Admin access required")


def log_event(event, details):
    db.session.add(AuditLog(event=event, details=details))
    db.session.commit()

def parse_cc(cc_emails: str):
    if not cc_emails:
        return []
    return [e.strip() for e in cc_emails.split(",") if e.strip()]


def deliver_email(subject, to_recipients, cc_recipients, body):
    """
    Gmail SMTP integration.
    Set env vars:
      - GMAIL_SMTP_USER (your gmail)
      - GMAIL_SMTP_APP_PASSWORD (App Password)
    Optional:
      - GMAIL_SMTP_HOST (default smtp.gmail.com)
      - GMAIL_SMTP_PORT (default 587)
    """
    to_recipients = [r for r in (to_recipients or []) if r]
    cc_recipients = [r for r in (cc_recipients or []) if r]
    if not to_recipients:
        return False, "No recipients"

    smtp_user = os.getenv("GMAIL_SMTP_USER", "").strip()
    smtp_pass = os.getenv("GMAIL_SMTP_APP_PASSWORD", "").strip()
    host = os.getenv("GMAIL_SMTP_HOST", "smtp.gmail.com").strip()
    port = int(os.getenv("GMAIL_SMTP_PORT", "587").strip())

    if not smtp_user or not smtp_pass:
        # Fall back to console so development still works.
        print("\n=== EMAIL (console fallback) ===")
        print("Subject:", subject)
        print("To:", ", ".join(to_recipients))
        if cc_recipients:
            print("Cc:", ", ".join(cc_recipients))
        print(body)
        print("=== END EMAIL ===\n")
        return True, None

    settings = get_email_settings()
    from_email = (settings.from_email or smtp_user).strip()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = ", ".join(to_recipients)
    if cc_recipients:
        msg["Cc"] = ", ".join(cc_recipients)
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True, None
    except Exception as exc:
        return False, str(exc)


def get_email_settings():
    settings = EmailSettings.query.get(1)
    if not settings:
        settings = EmailSettings(id=1)
        db.session.add(settings)
        db.session.commit()
    return settings


def queue_or_send_email(event_type, to_email, subject, body, task=None, submission=None, cc_emails=""):
    settings = get_email_settings()
    signature = settings.signature or ""
    full_body = (body.rstrip() + "\n\n" + signature).strip()

    if settings.automated_emails_enabled:
        ok, err = deliver_email(subject, [to_email], parse_cc(cc_emails), full_body)
        if not ok:
            db.session.add(
                OutboxEmail(
                    event_type=event_type,
                    to_email=to_email,
                    cc_emails=cc_emails,
                    subject=subject,
                    body=full_body,
                    status="Failed",
                    last_error=err,
                    task_id=task.id if task else None,
                    submission_id=submission.id if submission else None,
                )
            )
            db.session.commit()
            return False
        db.session.add(
            OutboxEmail(
                event_type=event_type,
                to_email=to_email,
                cc_emails=cc_emails,
                subject=subject,
                body=full_body,
                status="Sent",
                sent_at=datetime.utcnow(),
                task_id=task.id if task else None,
                submission_id=submission.id if submission else None,
            )
        )
        db.session.commit()
        return True

    db.session.add(
        OutboxEmail(
            event_type=event_type,
            to_email=to_email,
            cc_emails=cc_emails,
            subject=subject,
            body=full_body,
            status="Pending",
            task_id=task.id if task else None,
            submission_id=submission.id if submission else None,
        )
    )
    db.session.commit()
    return True


def send_task_created_email(task):
    portal_link = "http://localhost:5000/login"
    subject, body = compose_task_created_email(task, portal_link)
    queue_or_send_email("task_created", task.responsible_user.email, subject, body, task=task)


def send_submission_email_to_admin(task, submission):
    admin = User.query.filter_by(role="admin").first()
    subject, body = compose_submission_email(task, submission)
    queue_or_send_email("task_submitted", admin.email, subject, body, task=task, submission=submission)


def send_approval_email(task):
    subject, body = compose_approval_email(task)
    queue_or_send_email("task_approved", task.responsible_user.email, subject, body, task=task)


def send_rejection_email(task, comment):
    subject, body = compose_rejection_email(task, comment)
    queue_or_send_email("task_rejected", task.responsible_user.email, subject, body, task=task)


def compose_task_created_email(task, portal_link):
    settings = get_email_settings()
    if settings.ai_composer_enabled:
        subject = f"Action required: {task.topic} (due {task.target_date.strftime('%d-%b')})"
        body = (
            f"Hi {task.responsible_user.name},\n\n"
            f"A new task has been assigned to you.\n\n"
            f"- Function: {task.function_name}\n"
            f"- Sub-function: {task.sub_function or '-'}\n"
            f"- Topic: {task.topic}\n"
            f"- Target date: {task.target_date.strftime('%d-%b-%Y')}\n"
            f"- Periodicity: {task.periodicity}\n\n"
            f"Please upload the evidence before the due date to keep your compliance green.\n\n"
            f"Portal link: {portal_link}"
        )
        return subject, body
    subject = "New Task Assigned"
    body = (
        f"New task assigned.\n"
        f"Function: {task.function_name}\n"
        f"Sub-function: {task.sub_function or '-'}\n"
        f"Topic: {task.topic}\n"
        f"Target Date: {task.target_date}\n"
        f"Periodicity: {task.periodicity}\n"
        f"Portal: {portal_link}"
    )
    return subject, body


def compose_submission_email(task, submission):
    settings = get_email_settings()
    if settings.ai_composer_enabled:
        subject = f"Submission received: Task #{task.id} ({task.responsible_user.name})"
        body = (
            f"Hi Admin,\n\n"
            f"Evidence has been submitted and is ready for review.\n\n"
            f"- Task: #{task.id}\n"
            f"- Topic: {task.topic}\n"
            f"- Responsible: {task.responsible_user.name} ({task.responsible_user.email})\n"
            f"- Submitted at: {submission.submitted_at.strftime('%d-%b-%Y %H:%M UTC')}\n"
            f"- File path: {submission.sharepoint_path}\n\n"
            f"Please approve or reject from the Tasks page."
        )
        return subject, body
    return "Task Submitted for Approval", (
        f"Task {task.id} has been submitted by {task.responsible_user.email}\n"
        f"Topic: {task.topic}\n"
        f"Submitted At: {submission.submitted_at}\n"
        f"File: {submission.sharepoint_path}"
    )


def compose_approval_email(task):
    settings = get_email_settings()
    if settings.ai_composer_enabled:
        subject = f"Approved: {task.topic}"
        body = (
            f"Hi {task.responsible_user.name},\n\n"
            f"Great job — your submission has been approved.\n\n"
            f"- Task: #{task.id}\n"
            f"- Topic: {task.topic}\n\n"
            f"Thank you for closing this on time."
        )
        return subject, body
    return "Task Approved", f"Task {task.id} - {task.topic} has been approved and closed."


def compose_rejection_email(task, comment):
    settings = get_email_settings()
    if settings.ai_composer_enabled:
        subject = f"Needs update: {task.topic} (rejected)"
        body = (
            f"Hi {task.responsible_user.name},\n\n"
            f"Your submission for the below task was rejected.\n\n"
            f"- Task: #{task.id}\n"
            f"- Topic: {task.topic}\n\n"
            f"Comments from Admin:\n{comment}\n\n"
            f"Please resubmit as soon as possible to avoid escalation."
        )
        return subject, body
    return "Task Rejected", f"Task {task.id} was rejected.\nComments: {comment}"


def compose_motivation_email(user):
    settings = get_email_settings()
    tasks = Task.query.filter_by(responsible_user_id=user.id).all()
    open_count = sum(1 for t in tasks if t.status in ["Open", "Rejected"])
    overdue_count = sum(1 for t in tasks if t.status == "Overdue")
    submitted_count = sum(1 for t in tasks if t.status == "Submitted")
    closed_count = sum(1 for t in tasks if t.status == "Closed")

    if settings.ai_composer_enabled:
        subject = f"Your task status snapshot: {open_count} open, {overdue_count} overdue"
        body = (
            f"Hi {user.name},\n\n"
            f"Here’s your latest snapshot from the portal:\n"
            f"- Open/Rejected: {open_count}\n"
            f"- Submitted (awaiting approval): {submitted_count}\n"
            f"- Overdue: {overdue_count}\n"
            f"- Closed: {closed_count}\n\n"
            f"If you can clear even one open item today, you’ll stay ahead of escalations.\n"
            f"Log in and upload evidence where needed."
        )
        return subject, body
    subject = "Task reminder"
    body = f"Hi {user.name}, you have {open_count} open tasks and {overdue_count} overdue tasks. Please login and update."
    return subject, body


def generate_temp_password():
    return secrets.token_urlsafe(9)


def compose_user_welcome_email(user, temp_password):
    settings = get_email_settings()
    portal_link = "http://localhost:5000/login"
    if settings.ai_composer_enabled:
        subject = "Welcome – your temporary portal password"
        body = (
            f"Hi {user.name},\n\n"
            f"Your account has been created in the Task Tracking Portal.\n\n"
            f"Login: {user.email}\n"
            f"Temporary password: {temp_password}\n\n"
            f"You will be prompted to change your password on first login.\n\n"
            f"Portal link: {portal_link}"
        )
        return subject, body
    return "Portal access", f"Login: {user.email}\nTemp password: {temp_password}\nPortal: {portal_link}"


def add_periodicity(target_date, periodicity):
    if periodicity == "Monthly":
        return target_date + timedelta(days=30)
    if periodicity == "Quarterly":
        return target_date + timedelta(days=90)
    if periodicity == "Half-Yearly":
        return target_date + timedelta(days=182)
    if periodicity == "Yearly":
        return target_date + timedelta(days=365)
    return target_date


def regenerate_task(task):
    new_target = add_periodicity(task.target_date, task.periodicity)
    new_task = Task(
        function_name=task.function_name,
        sub_function=task.sub_function,
        topic=task.topic,
        target_date=new_target,
        periodicity=task.periodicity,
        responsible_user_id=task.responsible_user_id,
        created_by_id=task.created_by_id,
        status="Open",
        source_task_id=task.id,
    )
    db.session.add(new_task)
    db.session.commit()
    send_task_created_email(new_task)
    log_event("task_regenerated", f"Task {new_task.id} regenerated from {task.id}.")


def ensure_seed_data():
    if not EscalationConfig.query.get(1):
        db.session.add(EscalationConfig(id=1))
    if not EmailSettings.query.get(1):
        db.session.add(EmailSettings(id=1))
    if not User.query.filter_by(email="admin@company.com").first():
        db.session.add(
            User(
                name="Admin User",
                email="admin@company.com",
                password_hash=generate_password_hash("admin123"),
                role="admin",
            )
        )
    db.session.commit()


def ensure_gdrive_structure():
    current_year = datetime.utcnow().year
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for fn in FUNCTIONS:
        if fn == "HR":
            for sub in HR_SUB_FUNCTIONS:
                base = GDRIVE_DIR / "HR" / sub / str(current_year)
                for m in months:
                    (base / m).mkdir(parents=True, exist_ok=True)
        else:
            base = GDRIVE_DIR / fn / str(current_year)
            for m in months:
                (base / m).mkdir(parents=True, exist_ok=True)


def build_gdrive_path(task, dt):
    month = dt.strftime("%b")
    year = dt.strftime("%Y")
    if task.function_name == "HR":
        return GDRIVE_DIR / "HR" / (task.sub_function or "General") / year / month
    return GDRIVE_DIR / task.function_name / year / month


def run_escalation_engine():
    config = EscalationConfig.query.get(1)
    if not config:
        return
    today = datetime.utcnow().date()
    admin = User.query.filter_by(role="admin").first()
    tasks = Task.query.filter(Task.status.in_(["Open", "Rejected", "Overdue"])).all()
    for task in tasks:
        due = task.target_date
        days_to_due = (due - today).days
        days_after_due = (today - due).days

        # Reminder emails before due date.
        if task.status in ["Open", "Rejected"]:
            if days_to_due in {config.reminder_days_1, config.reminder_days_2}:
                queue_or_send_email(
                    "reminder",
                    task.responsible_user.email,
                    "Task reminder",
                    f"Reminder: Task #{task.id} '{task.topic}' is due on {task.target_date.strftime('%d-%b-%Y')}.",
                    task=task,
                )
                log_escalation(task, 0, [task.responsible_user.email], f"Reminder {days_to_due} days before due date")

        # Level 1 escalation on due date.
        if days_after_due >= 0 and task.escalation_level_sent < 1 and task.status != "Submitted":
            task.status = "Overdue"
            recipients = [task.responsible_user.email]
            queue_or_send_email(
                "escalation_l1",
                task.responsible_user.email,
                "Overdue task – Level 1",
                f"Task #{task.id} '{task.topic}' is overdue as of {task.target_date.strftime('%d-%b-%Y')}. Please submit evidence immediately.",
                task=task,
            )
            task.escalation_level_sent = 1
            log_escalation(task, 1, recipients, "Level 1 escalation")

        # Level 2 escalation.
        if days_after_due >= config.level_2_days and task.escalation_level_sent < 2 and task.status != "Submitted":
            recipients = [task.responsible_user.email]
            if config.include_reporting_manager_l2:
                recipients.append(task.responsible_user.reporting_manager_email)
            queue_or_send_email(
                "escalation_l2",
                task.responsible_user.email,
                "Overdue task – Level 2",
                f"Task #{task.id} '{task.topic}' is still overdue. Escalation Level 2 triggered.",
                task=task,
                cc_emails=", ".join([r for r in recipients[1:] if r]),
            )
            task.escalation_level_sent = 2
            log_escalation(task, 2, recipients, "Level 2 escalation")

        # Level 3 escalation.
        if days_after_due >= config.level_3_days and task.escalation_level_sent < 3 and task.status != "Submitted":
            recipients = [task.responsible_user.email]
            if config.include_reporting_manager_l2:
                recipients.append(task.responsible_user.reporting_manager_email)
            if config.include_function_head_l3:
                recipients.append(task.responsible_user.function_head_email)
            recipients.append(admin.email if admin else "")
            extra = [r.strip() for r in config.extra_recipients.split(",") if r.strip()]
            recipients.extend(extra)
            queue_or_send_email(
                "escalation_l3",
                task.responsible_user.email,
                "Overdue task – Level 3",
                f"Task #{task.id} '{task.topic}' has reached final escalation level.",
                task=task,
                cc_emails=", ".join([r for r in recipients[1:] if r]),
            )
            task.escalation_level_sent = 3
            log_escalation(task, 3, recipients, "Level 3 escalation")

    db.session.commit()


def log_escalation(task, level, recipients, message):
    db.session.add(
        EscalationLog(
            task_id=task.id,
            level=level,
            recipients=", ".join([r for r in recipients if r]),
            message=message,
        )
    )
    db.session.commit()


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
