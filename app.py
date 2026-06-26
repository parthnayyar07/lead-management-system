import os
import uuid
import json
import urllib.request
from datetime import datetime

from flask import Flask, request, redirect, render_template, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

database_url = os.environ.get("DATABASE_URL", "sqlite:///leads.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 280}
db = SQLAlchemy(app)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5000")
DESTINATION_LINK = "https://www.google.com"


class Lead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    company = db.Column(db.String(120))
    requirement = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    tracking_id = db.Column(db.String(36), unique=True, default=lambda: str(uuid.uuid4()))
    email_sent = db.Column(db.Boolean, default=False)
    email_opened = db.Column(db.Boolean, default=False)
    link_clicked = db.Column(db.Boolean, default=False)

    category = db.Column(db.String(50))
    priority = db.Column(db.String(20))


with app.app_context():
    db.create_all()


def classify_requirement(text):
    text_lower = text.lower()

    categories = {
        "AI Automation": ["chatbot", "ai", "automation", "automate", "machine learning", "ml"],
        "Web Development": ["website", "web app", "webpage", "landing page", "web development"],
        "App Development": ["mobile app", "android", "ios app", "app development"],
        "Data & Analytics": ["dashboard", "analytics", "data", "report", "insights"],
        "Marketing": ["marketing", "seo", "ads", "campaign", "social media"],
    }

    category = "General Inquiry"
    for cat, keywords in categories.items():
        if any(kw in text_lower for kw in keywords):
            category = cat
            break

    urgent_words = ["urgent", "asap", "immediately", "critical", "now"]
    priority = "High" if any(w in text_lower for w in urgent_words) else (
        "High" if category != "General Inquiry" else "Medium"
    )

    return category, priority


def send_lead_email(lead):
    if not RESEND_API_KEY:
        print("WARNING: RESEND_API_KEY not set")
        return False

    tracking_pixel_url = f"{BASE_URL}/track/open/{lead.tracking_id}"
    tracking_click_url = f"{BASE_URL}/track/click/{lead.tracking_id}"

    html_body = f"""
    <p>Hi {lead.name},</p>
    <p>Thank you for reaching out.</p>
    <p>We received your requirement: "{lead.requirement}"</p>
    <p><a href="{tracking_click_url}">Learn more</a></p>
    <p>Regards,<br>Team</p>
    <img src="{tracking_pixel_url}" width="1" height="1" style="display:none;">
    """

    payload = json.dumps({
        "from": "onboarding@resend.dev",
        "to": [lead.email],
        "subject": "Thanks for reaching out!",
        "html": html_body
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            print(response.read())
        return True
    except Exception as e:
        print(f"Email send failed: {e}")
        return False


@app.route("/")
def home():
    return render_template("form.html")


@app.route("/submit", methods=["POST"])
def submit():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    company = request.form.get("company", "").strip()
    requirement = request.form.get("requirement", "").strip()

    if not name or not email or not phone or not requirement:
        return "Missing required fields", 400

    category, priority = classify_requirement(requirement)

    lead = Lead(
        name=name, email=email, phone=phone,
        company=company, requirement=requirement,
        category=category, priority=priority
    )
    db.session.add(lead)
    db.session.commit()

    try:
        sent = send_lead_email(lead)
    except Exception as e:
        print(f"Email sending crashed: {e}")
        sent = False
    lead.email_sent = sent
    db.session.commit()

    return render_template("thank_you.html", name=name)


@app.route("/track/open/<tracking_id>")
def track_open(tracking_id):
    lead = Lead.query.filter_by(tracking_id=tracking_id).first()
    if lead:
        lead.email_opened = True
        db.session.commit()

    pixel = bytes.fromhex(
        "47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002023b"
    )
    return Response(pixel, mimetype="image/gif")


@app.route("/track/click/<tracking_id>")
def track_click(tracking_id):
    lead = Lead.query.filter_by(tracking_id=tracking_id).first()
    if lead:
        lead.link_clicked = True
        db.session.commit()
    return redirect(DESTINATION_LINK)


@app.route("/dashboard")
def dashboard():
    leads = Lead.query.all()
    total_leads = len(leads)
    emails_sent = sum(1 for l in leads if l.email_sent)
    emails_opened = sum(1 for l in leads if l.email_opened)
    links_clicked = sum(1 for l in leads if l.link_clicked)

    open_rate = round((emails_opened / emails_sent * 100), 1) if emails_sent else 0
    click_rate = round((links_clicked / emails_sent * 100), 1) if emails_sent else 0

    return render_template(
        "dashboard.html",
        total_leads=total_leads,
        emails_sent=emails_sent,
        emails_opened=emails_opened,
        links_clicked=links_clicked,
        open_rate=open_rate,
        click_rate=click_rate,
        leads=leads
    )


@app.route("/api/leads")
def api_leads():
    leads = Lead.query.all()
    return jsonify([{
        "name": l.name, "email": l.email, "phone": l.phone,
        "company": l.company, "requirement": l.requirement,
        "category": l.category, "priority": l.priority,
        "email_sent": l.email_sent, "email_opened": l.email_opened,
        "link_clicked": l.link_clicked,
        "created_at": l.created_at.isoformat()
    } for l in leads])


if __name__ == "__main__":
    app.run(debug=True, port=5000)