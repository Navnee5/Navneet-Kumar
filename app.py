import os, sqlite3, hashlib, secrets, hmac, io
from datetime import date, datetime
from functools import wraps
from flask import Flask, request, redirect, url_for, session, flash, render_template, jsonify, send_file
from werkzeug.utils import secure_filename
from openpyxl import load_workbook, Workbook

APP_DIR=os.path.dirname(os.path.abspath(__file__))
DB_PATH=os.path.join(APP_DIR,"attendance.db")
DATABASE_URL=os.environ.get("DATABASE_URL", "").strip()
UPLOAD_FOLDER=os.path.join(APP_DIR,"uploads")
os.makedirs(UPLOAD_FOLDER,exist_ok=True)

app=Flask(__name__)
app.secret_key=os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("RENDER", "").lower() == "true"
app.config["MAX_CONTENT_LENGTH"]=10*1024*1024

class PostgresConnection:
    """Small compatibility wrapper so the app can use qmark SQL on PostgreSQL."""
    def __init__(self, connection): self.connection=connection
    def execute(self, sql, params=()):
        return self.connection.execute(sql.replace("?", "%s"), params)
    def __enter__(self): self.connection.__enter__(); return self
    def __exit__(self, *args): return self.connection.__exit__(*args)

def db():
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row
        return PostgresConnection(psycopg.connect(DATABASE_URL, row_factory=dict_row))
    con=sqlite3.connect(DB_PATH)
    con.row_factory=sqlite3.Row
    return con

def hash_pw(password, salt=None):
    salt=salt or secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac("sha256",password.encode(),salt.encode(),240000).hex()
    return salt+"$"+digest

def verify_pw(password, stored):
    try:
        salt,digest=stored.split("$",1)
        return hmac.compare_digest(hash_pw(password,salt).split("$",1)[1],digest)
    except Exception:
        return False

def init_db():
    if DATABASE_URL:
        statements = [
          """CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, role TEXT NOT NULL, trainee_id TEXT UNIQUE,
            full_name TEXT, department TEXT)""",
          """CREATE TABLE IF NOT EXISTS attendance (
            id BIGSERIAL PRIMARY KEY, trainee_id TEXT NOT NULL,
            attendance_date TEXT NOT NULL, status TEXT NOT NULL,
            UNIQUE(trainee_id,attendance_date))""",
          """CREATE TABLE IF NOT EXISTS trainees (
            trainee_id TEXT PRIMARY KEY, full_name TEXT NOT NULL, department TEXT NOT NULL)"""
        ]
        with db() as c:
            for statement in statements: c.execute(statement)
            if not c.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
                admin_password=os.environ.get("ADMIN_PASSWORD", "")
                if not admin_password:
                    admin_password=secrets.token_urlsafe(18)
                    print("INITIAL ADMIN PASSWORD (save it now):", admin_password)
                c.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                          ("admin",hash_pw(admin_password),"admin"))
    else:
        with db() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS users(
              id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
              password_hash TEXT NOT NULL, role TEXT NOT NULL, trainee_id TEXT UNIQUE,
              full_name TEXT, department TEXT
            );
            CREATE TABLE IF NOT EXISTS attendance(
              id INTEGER PRIMARY KEY AUTOINCREMENT, trainee_id TEXT NOT NULL,
              attendance_date TEXT NOT NULL, status TEXT NOT NULL,
              UNIQUE(trainee_id,attendance_date)
            );
            CREATE TABLE IF NOT EXISTS trainees(
              trainee_id TEXT PRIMARY KEY, full_name TEXT NOT NULL, department TEXT NOT NULL
            );
            """)
            if not c.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
                c.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                          ("admin",hash_pw("Admin@123"),"admin"))

init_db()

def login_required(role=None):
    def deco(fn):
        @wraps(fn)
        def wrapped(*args,**kwargs):
            if not session.get("user_id"):
                return redirect(url_for("login"))
            if role and session.get("role")!=role:
                flash("You are not authorized to view that page.","error")
                return redirect(url_for("home"))
            return fn(*args,**kwargs)
        return wrapped
    return deco

@app.context_processor
def inject_user():
    return {"current_user":session.get("username"),"current_role":session.get("role")}

@app.route("/")
def home():
    if not session.get("user_id"): return redirect(url_for("login"))
    return redirect(url_for("admin_dashboard" if session.get("role")=="admin" else "trainee_dashboard"))

@app.route("/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        username=request.form.get("username","").strip()
        password=request.form.get("password","")
        with db() as c: user=c.execute("SELECT * FROM users WHERE username=?",(username,)).fetchone()
        if user and verify_pw(password,user["password_hash"]):
            session.clear()
            session.update(user_id=user["id"],username=user["username"],role=user["role"],trainee_id=user["trainee_id"])
            return redirect(url_for("home"))
        flash("Invalid username or password.","error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/admin")
@login_required("admin")
def admin_dashboard():
    selected=request.args.get("date",date.today().isoformat())
    with db() as c:
        total=c.execute("SELECT COUNT(*) n FROM trainees").fetchone()["n"]
        rows=c.execute("""SELECT a.status,COUNT(*) n FROM attendance a
                          WHERE a.attendance_date=? GROUP BY a.status""",(selected,)).fetchall()
        counts={r["status"]:r["n"] for r in rows}
        departments=c.execute("""SELECT t.department,
          SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END) present,
          SUM(CASE WHEN a.status='Absent' THEN 1 ELSE 0 END) absent,
          SUM(CASE WHEN a.status='Leave' THEN 1 ELSE 0 END) leave_count
          FROM trainees t LEFT JOIN attendance a ON a.trainee_id=t.trainee_id AND a.attendance_date=?
          GROUP BY t.department ORDER BY t.department""",(selected,)).fetchall()
        records=c.execute("""SELECT t.trainee_id,t.full_name,t.department,COALESCE(a.status,'Not Marked') status
          FROM trainees t LEFT JOIN attendance a ON a.trainee_id=t.trainee_id AND a.attendance_date=?
          ORDER BY t.department,t.full_name""",(selected,)).fetchall()
    return render_template("admin.html",selected=selected,total=total,
      present=counts.get("Present",0),absent=counts.get("Absent",0),leave_count=counts.get("Leave",0),
      departments=[dict(r) for r in departments],records=[dict(r) for r in records])

@app.route("/admin/upload",methods=["POST"])
@login_required("admin")
def upload_excel():
    f=request.files.get("file")
    if not f or not f.filename.lower().endswith((".xlsx",".xlsm")):
        flash("Please upload an Excel .xlsx file.","error"); return redirect(url_for("admin_dashboard"))
    try:
        wb=load_workbook(f, data_only=True)
        ws=wb.active
        headers=[str(x.value or "").strip().lower() for x in ws[1]]
        required=["trainee_id","name","department","date","status"]
        missing=[x for x in required if x not in headers]
        if missing:
            flash("Missing columns: "+", ".join(missing)+". Download the template and use its column headings.","error")
            return redirect(url_for("admin_dashboard"))
        ix={name:headers.index(name) for name in required}
        imported=0
        with db() as c:
            for row in ws.iter_rows(min_row=2,values_only=True):
                if not row or all(v is None for v in row): continue
                tid=str(row[ix["trainee_id"]] or "").strip()
                name=str(row[ix["name"]] or "").strip()
                dept=str(row[ix["department"]] or "").strip()
                raw_date=row[ix["date"]]
                status=str(row[ix["status"]] or "").strip().title()
                if not (tid and name and dept and raw_date): continue
                if isinstance(raw_date,datetime): day=raw_date.date().isoformat()
                elif isinstance(raw_date,date): day=raw_date.isoformat()
                else:
                    try: day=datetime.strptime(str(raw_date).strip()[:10],"%Y-%m-%d").date().isoformat()
                    except ValueError: day=datetime.strptime(str(raw_date).strip(),"%d-%m-%Y").date().isoformat()
                if status not in ("Present","Absent","Leave"): continue
                c.execute("""INSERT INTO trainees(trainee_id,full_name,department) VALUES(?,?,?)
                  ON CONFLICT(trainee_id) DO UPDATE SET full_name=excluded.full_name,department=excluded.department""",(tid,name,dept))
                c.execute("""INSERT INTO attendance(trainee_id,attendance_date,status) VALUES(?,?,?)
                  ON CONFLICT(trainee_id,attendance_date) DO UPDATE SET status=excluded.status""",(tid,day,status))
                imported+=1
            # Create trainee login accounts for new trainee IDs. Temporary password = trainee ID.
            trainees=c.execute("SELECT trainee_id,full_name,department FROM trainees").fetchall()
            for t in trainees:
                username=t["trainee_id"]
                if not c.execute("SELECT 1 FROM users WHERE trainee_id=?",(username,)).fetchone():
                    c.execute("INSERT INTO users(username,password_hash,role,trainee_id,full_name,department) VALUES(?,?,?,?,?,?)",
                      (username,hash_pw(username),"trainee",username,t["full_name"],t["department"]))
        flash(f"Import complete: {imported} attendance rows processed. New trainee login: username = Trainee ID; temporary password = Trainee ID. Ask trainees to change it after first login (password change page).","success")
    except Exception as e:
        flash("Could not read the workbook. Check the template and date format. Details: "+str(e),"error")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/template")
@login_required("admin")
def template():
    wb=Workbook(); ws=wb.active; ws.title="Attendance"
    ws.append(["trainee_id","name","department","date","status"])
    ws.append(["TML001","Amit Kumar","Front Axle",date.today().isoformat(),"Present"])
    ws.append(["TML002","Ravi Singh","Engine",date.today().isoformat(),"Absent"])
    out=io.BytesIO(); wb.save(out); out.seek(0)
    return send_file(out,as_attachment=True,download_name="attendance_upload_template.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/admin/trainees")
@login_required("admin")
def trainees_page():
    with db() as c: rows=c.execute("SELECT * FROM trainees ORDER BY department,full_name").fetchall()
    return render_template("trainees.html",trainees=[dict(r) for r in rows])

@app.route("/trainee")
@login_required("trainee")
def trainee_dashboard():
    tid=session["trainee_id"]
    with db() as c:
        person=c.execute("SELECT * FROM trainees WHERE trainee_id=?",(tid,)).fetchone()
        rows=c.execute("SELECT attendance_date,status FROM attendance WHERE trainee_id=? ORDER BY attendance_date DESC LIMIT 60",(tid,)).fetchall()
        totals=c.execute("SELECT status,COUNT(*) n FROM attendance WHERE trainee_id=? GROUP BY status",(tid,)).fetchall()
    counts={r["status"]:r["n"] for r in totals}
    return render_template("trainee.html",person=dict(person) if person else {},rows=[dict(r) for r in rows],
      present=counts.get("Present",0),absent=counts.get("Absent",0),leave_count=counts.get("Leave",0))

@app.route("/change-password",methods=["GET","POST"])
@login_required()
def change_password():
    if request.method=="POST":
        p1=request.form.get("password",""); p2=request.form.get("confirm","")
        if len(p1)<8: flash("Password must be at least 8 characters.","error")
        elif p1!=p2: flash("Passwords do not match.","error")
        else:
            with db() as c: c.execute("UPDATE users SET password_hash=? WHERE id=?",(hash_pw(p1),session["user_id"]))
            flash("Password updated successfully.","success")
            return redirect(url_for("home"))
    return render_template("change_password.html")

@app.route("/api/department-summary")
@login_required("admin")
def department_summary():
    day=request.args.get("date",date.today().isoformat())
    with db() as c:
        rows=c.execute("""SELECT t.department,
          SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END) present,
          SUM(CASE WHEN a.status='Absent' THEN 1 ELSE 0 END) absent,
          SUM(CASE WHEN a.status='Leave' THEN 1 ELSE 0 END) leave_count
          FROM trainees t LEFT JOIN attendance a ON a.trainee_id=t.trainee_id AND a.attendance_date=?
          GROUP BY t.department ORDER BY t.department""",(day,)).fetchall()
    return jsonify([dict(r) for r in rows])

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)
