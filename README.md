# Task Tracking Application (Python + HTML + CSS)

Enterprise-style task tracking portal with:

- Admin task creation, edit, delete, and status monitoring
- User-only assigned task visibility with evidence upload
- Submission -> approval/rejection workflow with comments
- Auto-regeneration of periodic tasks after closure
- Escalation matrix with configurable timelines and recipients
- SharePoint-style folder routing for uploaded files
- Gmail SMTP email delivery (optional) + admin-controlled automation/outbox
- Audit trail + escalation logs

## Tech Stack

- Backend: Flask + SQLite
- Frontend: Jinja templates + plain HTML/CSS
- File storage: local "Google Drive-style" structure under `gdrive/`

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open [http://localhost:5000](http://localhost:5000).

## Demo Users

- Admin: `admin@company.com` / `admin123`
- User (HR): `hr.user@company.com` / `user123`
- User (IT): `it.user@company.com` / `user123`

## Requirement Coverage Notes

- Emails support Gmail SMTP via env vars. If automation is OFF, emails are queued in Email Center for admin review/edit.
- Drive movement is modeled as automatic movement into function/sub-function/year/month folder hierarchy under `gdrive/`.
- Escalation checks are executed when dashboards are opened. For production, run this through a scheduler/cron.
- Downloadable Excel/PDF reports are not included in this version but can be added.

## Gmail SMTP setup

Use a Gmail **App Password** (recommended).

Set environment variables:

- `GMAIL_SMTP_USER`
- `GMAIL_SMTP_APP_PASSWORD`

If these are not set, emails fall back to console output for development.
