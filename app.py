"""VisualMCQ human evaluation: a minimal annotation server.

Annotators log in, pick a dataset and answer hard-bank questions one at a time. Every question goes to at most one
annotator (no overlap). Within a dataset the next question is drawn from the class (implant model / person) the
annotator has seen least, so any number of answers stays close to class-balanced. The admin page shows progress
and exports every response as CSV.
"""
import csv, io, json, os, random, sqlite3, time
from contextlib import closing
from collections import Counter, defaultdict
from datetime import datetime, timezone

from flask import (Flask, Response, abort, redirect, render_template, request, send_from_directory, session,
                   url_for)

DATA = os.environ.get("VMCQ_DATA", "data")
STATE = os.environ.get("VMCQ_STATE", "state")
USERS_FILE = os.environ.get("VMCQ_USERS", "config/users.json")

app = Flask(__name__)
app.secret_key = os.environ.get("VMCQ_SECRET", "vmcq-human-eval")   # signs the session cookie only

# ------------------------------------------------------------------ data
QUESTIONS = [json.loads(l) for l in open(os.path.join(DATA, "questions.jsonl"))]
Q = {q["uid"]: q for q in QUESTIONS}
BY_DATASET = defaultdict(list)
for q in QUESTIONS:
    BY_DATASET[q["dataset"]].append(q["uid"])
DATASETS = [d for d in ["ortho_hip", "ortho_knee", "physionet", "pacemakers", "market1501"] if d in BY_DATASET]
DATASETS += sorted(d for d in BY_DATASET if d not in DATASETS)
DS_NAME = {d: Q[BY_DATASET[d][0]]["dataset_name"] for d in DATASETS}
IMPLANT_DATASETS = [d for d in DATASETS if Q[BY_DATASET[d][0]]["domain"] == "implants"]
META = json.load(open(os.path.join(DATA, "meta.json"))) if os.path.exists(os.path.join(DATA, "meta.json")) else {}


def load_users():
    return json.load(open(USERS_FILE))   # re-read on every login, so edits apply without a restart


# ------------------------------------------------------------------ database
os.makedirs(STATE, exist_ok=True)
DB_PATH = os.path.join(STATE, "responses.sqlite")


def db():
    c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    return c


with closing(db()) as c:
    c.execute("PRAGMA journal_mode=WAL")
    # one row per question handed out; uid is the primary key, so a question can belong to one annotator only
    c.execute("""CREATE TABLE IF NOT EXISTS assignments (
        uid TEXT PRIMARY KEY, username TEXT NOT NULL, dataset TEXT NOT NULL,
        served_at REAL NOT NULL, answered_at REAL, picked_option INTEGER, seconds REAL)""")


def now():
    return time.time()


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if t else ""


# ------------------------------------------------------------------ assignment
def next_question(username, dataset):
    """The annotator's open question in this dataset, else a new one from their least-seen class."""
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE")      # serialises assignment across threads and processes
        row = c.execute("SELECT uid FROM assignments WHERE username=? AND dataset=? AND answered_at IS NULL "
                        "ORDER BY served_at LIMIT 1", (username, dataset)).fetchone()
        if row:
            c.execute("COMMIT")
            return row["uid"]
        taken = {r["uid"] for r in c.execute("SELECT uid FROM assignments WHERE dataset=?", (dataset,))}
        free = [u for u in BY_DATASET[dataset] if u not in taken]
        if not free:
            c.execute("COMMIT")
            return None
        mine = [Q[r["uid"]] for r in c.execute("SELECT uid FROM assignments WHERE username=? AND dataset=?",
                                                (username, dataset))]
        mine_cls = Counter(q["query_class"] for q in mine)
        all_cls = Counter(Q[u]["query_class"] for u in taken)
        seen_patients = {q["query_patient"] for q in mine if q.get("query_patient")}
        by_cls = defaultdict(list)
        for u in free:
            by_cls[Q[u]["query_class"]].append(u)
        # least-seen class for this annotator, then least-seen overall, then random
        cls = min(by_cls, key=lambda k: (mine_cls[k], all_cls[k], random.random()))
        pool = by_cls[cls]
        fresh = [u for u in pool if Q[u].get("query_patient") not in seen_patients]   # avoid repeating a patient
        uid = random.choice(fresh or pool)
        c.execute("INSERT INTO assignments (uid, username, dataset, served_at) VALUES (?,?,?,?)",
                  (uid, username, dataset, now()))
        c.execute("COMMIT")
        return uid
    except Exception:
        c.execute("ROLLBACK")
        raise
    finally:
        c.close()


def user_counts(username):
    with closing(db()) as c:
        rows = c.execute("SELECT dataset, COUNT(*) n FROM assignments WHERE username=? AND answered_at IS NOT NULL "
                         "GROUP BY dataset", (username,)).fetchall()
    return {r["dataset"]: r["n"] for r in rows}


def remaining(dataset):
    with closing(db()) as c:
        n = c.execute("SELECT COUNT(*) FROM assignments WHERE dataset=?", (dataset,)).fetchone()[0]
    return len(BY_DATASET[dataset]) - n


# ------------------------------------------------------------------ auth
def current_user():
    return session.get("user")


@app.route("/", methods=["GET"])
def index():
    u = current_user()
    if not u:
        return redirect(url_for("login"))
    return redirect(url_for("admin" if u["role"] == "admin" else "choose"))


@app.route("/login", methods=["GET", "POST"])
def login():
    err = None
    if request.method == "POST":
        name, pw = request.form.get("username", "").strip(), request.form.get("password", "")
        users = load_users()
        rec = users.get(name)
        if rec and rec.get("password") == pw:
            session["user"] = {"username": name, "role": rec.get("role", "annotator"),
                               "name": rec.get("name", name)}
            return redirect(url_for("index"))
        err = "Wrong username or password."
    return render_template("login.html", err=err)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def need(role=None):
    u = current_user()
    if not u:
        abort(redirect(url_for("login")))
    if role and u["role"] != role:
        abort(403)
    return u


# ------------------------------------------------------------------ annotator pages
@app.route("/choose")
def choose():
    u = need()
    counts = user_counts(u["username"])
    rows = [{"id": d, "name": DS_NAME[d], "mine": counts.get(d, 0), "left": remaining(d),
             "k": len(Q[BY_DATASET[d][0]]["options"])} for d in DATASETS]
    return render_template("choose.html", user=u, rows=rows, total=sum(counts.values()),
                           nota=META.get("nota", False))


@app.route("/annotate/<dataset>")
def annotate(dataset):
    u = need()
    if dataset not in BY_DATASET:
        abort(404)
    uid = next_question(u["username"], dataset)
    counts = user_counts(u["username"])
    if uid is None:
        return render_template("done.html", user=u, dataset=DS_NAME[dataset])
    q = Q[uid]
    return render_template("annotate.html", user=u, q=q, dataset=dataset, ds_name=DS_NAME[dataset],
                           n_here=counts.get(dataset, 0), n_total=sum(counts.values()))


@app.route("/answer", methods=["POST"])
def answer():
    u = need()
    uid, dataset = request.form.get("uid", ""), request.form.get("dataset", "")
    try:
        pick = int(request.form.get("pick", ""))
    except ValueError:
        return redirect(url_for("annotate", dataset=dataset))
    q = Q.get(uid)
    if not q or not 1 <= pick <= len(q["options"]) + (1 if q["nota_offered"] else 0):
        abort(400)
    with closing(db()) as c:
        row = c.execute("SELECT served_at FROM assignments WHERE uid=? AND username=? AND answered_at IS NULL",
                        (uid, u["username"])).fetchone()
        if row:   # ignores double submits and questions that are not this annotator's
            t = now()
            c.execute("UPDATE assignments SET answered_at=?, picked_option=?, seconds=? WHERE uid=?",
                      (t, pick, round(t - row["served_at"], 1), uid))
    return redirect(url_for("annotate", dataset=q["dataset"]))


@app.route("/img/<path:p>")
def img(p):
    need()
    return send_from_directory(os.path.join(DATA, "images"), p)


# ------------------------------------------------------------------ admin
def answered_rows():
    with closing(db()) as c:
        return c.execute("SELECT * FROM assignments WHERE answered_at IS NOT NULL ORDER BY answered_at").fetchall()


@app.route("/admin")
def admin():
    need("admin")
    users = load_users()
    rows = answered_rows()
    with closing(db()) as c:
        pend = c.execute("SELECT username, COUNT(*) n FROM assignments WHERE answered_at IS NULL "
                         "GROUP BY username").fetchall()
    pending = {r["username"]: r["n"] for r in pend}
    per = defaultdict(lambda: defaultdict(lambda: [0, 0]))     # user -> dataset -> [answered, correct]
    last = {}
    for r in rows:
        ok = r["picked_option"] == Q[r["uid"]]["correct_option"]
        for key in (r["username"], "__all__"):
            per[key][r["dataset"]][0] += 1
            per[key][r["dataset"]][1] += ok
        last[r["username"]] = r["answered_at"]

    def summary(key):
        d = per.get(key, {})
        cells = [d.get(ds, [0, 0]) for ds in DATASETS]
        imp = [d[ds] for ds in IMPLANT_DATASETS if d.get(ds, [0])[0]]
        n_imp, c_imp = sum(x[0] for x in imp), sum(x[1] for x in imp)
        return {"cells": cells, "n": sum(x[0] for x in cells),
                "mean": (100 * sum(x[1] / x[0] for x in imp) / len(imp)) if imp else None,
                "pooled": (100 * c_imp / n_imp) if n_imp else None, "n_imp_ds": len(imp)}

    annot = [u for u, r in users.items() if r.get("role", "annotator") != "admin"]
    annot += sorted(u for u in per if u != "__all__" and u not in annot)
    table = [{"username": u, "name": users.get(u, {}).get("name", u), "pending": pending.get(u, 0),
              "last": iso(last.get(u)), **summary(u)} for u in annot]
    return render_template("admin.html", user=current_user(), table=table, total=summary("__all__"),
                           datasets=[(d, DS_NAME[d], len(BY_DATASET[d]), remaining(d)) for d in DATASETS],
                           meta=META, n_resp=len(rows))


CSV_ANNOTATOR_SKIP = {"password", "role"}


@app.route("/admin/responses.csv")
def export_csv():
    need("admin")
    users = load_users()
    extra = sorted({k for r in users.values() for k in r if k not in CSV_ANNOTATOR_SKIP})
    kmax = max(len(q["options"]) for q in QUESTIONS)
    head = (["response_no", "username"] + [f"annotator_{k}" for k in extra] +
            ["domain", "bank", "dataset", "dataset_name", "split", "qid", "uid",
             "query_image", "query_class", "query_patient", "query_cam", "n_image_options", "nota_offered"] +
            [f"cand{i}_{f}" for i in range(1, kmax + 1) for f in ("image", "class", "system_rank")] +
            ["correct_option", "correct_class", "picked_option", "picked_class", "is_correct",
             "picked_nota", "answer_is_nota", "system_top1", "nota_design",
             "served_at", "answered_at", "seconds_taken", "annotator_seq_in_dataset", "annotator_seq_overall"])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(head)
    seq_ds, seq_all = Counter(), Counter()

    def cls_of(q, opt):
        return "None of the above" if opt == len(q["options"]) + 1 else q["options"][opt - 1]["class"]

    for i, r in enumerate(answered_rows(), 1):
        q, un = Q[r["uid"]], r["username"]
        seq_ds[(un, r["dataset"])] += 1
        seq_all[un] += 1
        rec = users.get(un, {})
        cands = []
        for j in range(kmax):
            o = q["options"][j] if j < len(q["options"]) else {}
            cands += [o.get("image", ""), o.get("class", ""), o.get("rank", "")]
        nota_no = len(q["options"]) + 1
        w.writerow([i, un] + [rec.get(k, "") for k in extra] +
                   [q["domain"], q["bank"], q["dataset"], q["dataset_name"], q["split"], q["qid"], q["uid"],
                    q["query_image"], q["query_class"], q.get("query_patient") or "", q.get("query_cam") or "",
                    len(q["options"]), int(q["nota_offered"])] + cands +
                   [q["correct_option"], cls_of(q, q["correct_option"]), r["picked_option"],
                    cls_of(q, r["picked_option"]), int(r["picked_option"] == q["correct_option"]),
                    int(r["picked_option"] == nota_no), int(q["correct_option"] == nota_no),
                    q.get("retrieval_top1") or "", q.get("nota_design") or "",
                    iso(r["served_at"]), iso(r["answered_at"]), r["seconds"],
                    seq_ds[(un, r["dataset"])], seq_all[un]])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=human_eval_responses_{stamp}.csv"})


@app.route("/healthz")
def healthz():
    return {"ok": True, "questions": len(QUESTIONS)}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8015)), debug=False)
