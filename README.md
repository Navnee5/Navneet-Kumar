# Trainee Attendance Management System

Flask-based attendance website with admin and trainee logins, Excel attendance import, daily summaries, department-wise summary, and trainee self-service attendance view.

## Deploy on Render

1. Push this repository to GitHub.
2. In Render, choose **New + → Blueprint** and connect this repository.
3. Render reads `render.yaml` and provisions the web service and PostgreSQL database.
4. When prompted for `ADMIN_PASSWORD`, enter a strong unique password. Do not share it in chat or commit it to GitHub.
5. Deploy. Open the generated `onrender.com` URL and sign in with username `admin` and the password you supplied.

The database URL and session secret are configured by Render. Attendance data is stored in PostgreSQL, not in the temporary web-service filesystem.

## Run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000. Local mode uses SQLite and the initial local admin login is `admin` / `Admin@123`; change this password immediately.

## Import attendance Excel

Use the headings exactly as shown:

`trainee_id | name | department | date | status`

Allowed status values: `Present`, `Absent`, `Leave`. Date format: `YYYY-MM-DD` or `DD-MM-YYYY`. One row represents one trainee for one date. Re-uploading a trainee/date updates that record. Missing attendance is displayed as **Not Marked**, not assumed Absent.

New trainee login: username = Trainee ID; temporary password = Trainee ID. Trainees should change their password after first login.

## Security notes

- Set a strong `ADMIN_PASSWORD` in hosting environment variables.
- Keep the repository and hosting environment free of passwords and real trainee personal data.
- Enable HTTPS (Render provides it automatically).
- Back up the PostgreSQL database regularly.
