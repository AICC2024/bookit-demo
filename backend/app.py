from flask import Flask, request, jsonify, redirect, send_from_directory, send_file
from flask_cors import CORS
import uuid
import os
from twilio.rest import Client
from dotenv import load_dotenv
from datetime import datetime, timedelta
from twilio.twiml.messaging_response import MessagingResponse

load_dotenv()

TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_API_KEY = os.getenv('TWILIO_API_KEY')
TWILIO_API_SECRET = os.getenv('TWILIO_API_SECRET')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')

twilio_client = Client(TWILIO_API_KEY, TWILIO_API_SECRET, TWILIO_ACCOUNT_SID)

app = Flask(__name__, static_url_path='/static', static_folder='static')
print("✅ Flask app is running with correct CORS and localhost binding (app.py)")
CORS(app, resources={r"/*": {"origins": "*"}}, allow_headers="*", expose_headers="*", supports_credentials=True)

# In-memory store for demo (replace with DB in prod)
appointments = {}

@app.route('/send-initial-message', methods=['POST'])
def send_initial_message():
    data = request.form
    phone = data['phone']
    name = data['name']
    missed_time = data['missed_time']
    token = str(uuid.uuid4())[:8]

    provider_name = data.get('provider_name', 'Jane Roberts, FNP')
    try:
        missed_dt = datetime.strptime(missed_time, "%A, %B %d, %Y at %I:%M %p")
    except ValueError:
        return jsonify({'error': 'Invalid missed_time format'}), 400

    option1 = (missed_dt + timedelta(days=1)).replace(hour=10, minute=0).strftime("%A, %B %d, %Y at %I:%M %p")
    option2 = (missed_dt + timedelta(days=2)).replace(hour=14, minute=30).strftime("%A, %B %d, %Y at %I:%M %p")

    facility_name = data.get('facility_name', '')
    logo_file = request.files.get('facility_logo')
    logo_filename = None

    if logo_file:
        logo_dir = os.path.join(app.static_folder, 'logo_uploads')
        os.makedirs(logo_dir, exist_ok=True)
        safe_name = os.path.basename(logo_file.filename).replace(" ", "_")
        logo_filename = f"{token}_{safe_name}"
        logo_path = os.path.join(logo_dir, logo_filename)
        logo_file.save(logo_path)
        os.chmod(logo_path, 0o644)

    appointments[token] = {
        'name': name,
        'phone': phone,
        'missed_time': missed_time,
        'status': 'link_sent',
        'facility_name': facility_name,
        'logo_filename': logo_filename,
        'provider_name': provider_name,
        'option1': option1,
        'option2': option2,
    }

    message_body = (
        f"Hello {name}, you missed your appointment on {missed_time}.\n\n"
        f"Reply 1 to reschedule \n"
        f"or \n"
        f"2 to have the office call you.\n\n"
        f"Thank you, {facility_name}"
    )

    try:
        message = twilio_client.messages.create(
            body=message_body,
            from_=TWILIO_PHONE_NUMBER,
            to=phone
        )
        print(f"✅ SMS sent to {phone}: SID={message.sid}")
    except Exception as e:
        print(f"❌ Failed to send SMS: {e}")
        return jsonify({'error': 'Failed to send SMS'}), 500

    return jsonify({'message': 'Initial message sent', 'token': token})

@app.route('/choose-time/<token>', methods=['GET'])
def serve_secure_link(token):
    if token in appointments:
        return redirect(f"http://localhost:3000/secure_link_page/?token={token}", code=302)
    return "Invalid or expired link", 404

@app.route('/confirm-time', methods=['POST'])
def confirm_time():
    data = request.json
    token = data['token']
    time_selected = data['time']
    if token in appointments:
        if time_selected == "3":
            option1 = (datetime.now() + timedelta(days=1)).replace(hour=10, minute=0).strftime("%A, %B %d, %Y at %I:%M %p")
            option2 = (datetime.now() + timedelta(days=2)).replace(hour=14, minute=30).strftime("%A, %B %d, %Y at %I:%M %p")
            appointments[token]['option1'] = option1
            appointments[token]['option2'] = option2
            return jsonify({'message': 'New options generated', 'newOptions': [option1, option2]})

        appointments[token]['status'] = 'confirmed'
        appointments[token]['confirmed_time'] = time_selected
        print(f"Confirmed appointment for {appointments[token]['name']} at {time_selected}")
        return jsonify({'message': 'Appointment confirmed'})
    return jsonify({'error': 'Invalid token'}), 400

@app.route('/get-new-options', methods=['POST'])
def get_new_options():
    data = request.json
    token = data.get('token')
    if not token or token not in appointments:
        return jsonify({'error': 'Invalid token'}), 400

    # Use current datetime to generate new future options
    now = datetime.now()
    option1 = (now + timedelta(days=1)).replace(hour=10, minute=0).strftime("%A, %B %d, %Y at %I:%M %p")
    option2 = (now + timedelta(days=2)).replace(hour=14, minute=30).strftime("%A, %B %d, %Y at %I:%M %p")

    # Save them for future use if needed
    appointments[token]['option1'] = option1
    appointments[token]['option2'] = option2

    return jsonify({'option1': option1, 'option2': option2})

# New route: /get-branding/<token>
@app.route('/get-branding/<token>', methods=['GET'])
def get_branding(token):
    appt = appointments.get(token)
    if not appt:
        return jsonify({'error': 'Invalid token'}), 404

    branding = {
        'facility_name': appt.get('facility_name'),
        'logo_url': f"http://127.0.0.1:5000/static/logo_uploads/{appt['logo_filename']}" if appt.get('logo_filename') else None,
        'missed_time': appt.get('missed_time'),
        'provider_name': appt.get('provider_name'),
        'option1': appt.get('option1'),
        'option2': appt.get('option2'),
    }
    return jsonify(branding)

# --- New route: /sms-webhook ---
@app.route('/sms-webhook', methods=['POST'])
def sms_webhook():
    from_number = request.form.get('From')
    body = request.form.get('Body', '').strip()
    print(f"📩 Incoming SMS from {from_number}: {body}")

    import re

    def normalize_number(number):
        return re.sub(r'\D', '', number)[-10:]  # Strip everything but last 10 digits

    token = None
    from_number_normalized = normalize_number(from_number)

    for t, appt in reversed(appointments.items()):
        appt_number_normalized = normalize_number(appt['phone'])
        if appt_number_normalized == from_number_normalized:
            token = t
            break

    resp = MessagingResponse()

    if not token:
        resp.message("Sorry, we couldn't find your appointment. Please contact the office.")
    elif body == "1":
        public_url = os.getenv("FRONTEND_PUBLIC_URL", "http://localhost:3000")
        secure_link = f"{public_url}/secure_link_page/?token={token}"
        resp.message(f"Thanks! Click the link to choose a new time:\n{secure_link}")
    elif body == "2":
        resp.message("A member of our team will call you soon. Or call us now at 615-867-5309.")
    else:
        resp.message("Sorry, please reply with 1 to reschedule or 2 to be contacted.")

    from flask import Response
    return Response(str(resp), mimetype="text/xml")


# Route to serve the secure link chatbot page directly from Flask's static directory
@app.route('/secure_link_page/')
def serve_chatbot():
    return send_from_directory('static/secure_link_page', 'index.html')

@app.route('/admin.html')
def serve_admin():
    return send_from_directory('static', 'admin.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)