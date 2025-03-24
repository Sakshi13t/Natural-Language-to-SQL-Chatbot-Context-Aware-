from flask import Flask, render_template, request, jsonify, session
from chatbot import get_bot_response, get_response
import uuid
from datetime import timedelta
import logging
import os
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

app.secret_key = "your_secret_key"
app.permanent_session_lifetime = timedelta(minutes=30)

# Set up logging
log_directory = "chat_logs"
os.makedirs(log_directory, exist_ok=True)
log_file = os.path.join(log_directory, "chat_history.log")

logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format='%(asctime)s - %(session_id)s - USER: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Global session_data to track per user session
session_data = {}

@app.before_request
def before_request():
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
    if session['session_id'] not in session_data:
        session_data[session['session_id']] = {'entities': {}, 'history': []}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    user_query = data.get("query")
    print("Incoming query:", user_query)

    if not user_query:
        return jsonify({"response": "Please enter a valid question."})
  
    session_id = session.get('session_id')
    if session_id not in session_data:
        session_data[session_id] = {'entities': {}, 'history': []}

    # ----- Check for predefined response -----
    predefined_reply = get_response(user_query.lower())
    if predefined_reply:
        # Save to session history
        session_data[session_id]['history'].append({
            "user": user_query,
            "bot": predefined_reply
        })

        # Log both user query and predefined response
        logging.info(
            f"\n==== New Chat ====\nUser: {user_query}\nBot: {predefined_reply}\n===================",
            extra={'session_id': session_id}
        )

        return jsonify({
            "response": predefined_reply,
            "query": user_query
        })

    try:
        # ----- Normal bot logic -----
        current_session = session_data[session_id]

        # Call bot response function
        bot_response = get_bot_response(user_query)
        print("Raw bot response:", bot_response)

        # Clean response
        if isinstance(bot_response, dict):
            formatted_response = bot_response.get('message', str(bot_response))
        else:
            lines = bot_response.split("\n")
            cleaned_lines = []
            seen = set()
            for line in lines:
                line = line.strip()
                if line and "None" not in line and line not in seen:
                    cleaned_lines.append(line)
                    seen.add(line)
            formatted_response = "\n".join(cleaned_lines)

        # Save to history
        current_session['history'].append({
            "user": user_query,
            "bot": formatted_response
        })

        # Log both query and bot response
        logging.info(
            f"\n==== New Chat ====\nUser: {user_query}\nBot: {formatted_response}\n===================",
            extra={'session_id': session_id}
        )

        return jsonify({
            "response": formatted_response,
            "query": user_query
        })

    except Exception as e:
        print("Error Occurred:", str(e))
        return jsonify({"response": f"Sorry, something went wrong. {str(e)}"})

@app.route("/feedback", methods=["POST"])
def feedback():
    data = request.get_json()
    user_query = data.get("query")
    bot_response = data.get("response")
    feedback_type = data.get("feedback")

    if not user_query or not bot_response or feedback_type not in [0, 1]:
        return jsonify({"message": "Incomplete feedback data."}), 400

    try:
        feedback_entry = (
            f"User Query: {user_query}\n"
            f"Bot Response:\n{bot_response}\n"
            f"Feedback: {'good' if feedback_type == 1 else 'bad'}\n"
            f"{'-'*50}\n"
        )
        file_name = 'good_feedback.txt' if feedback_type == 1 else 'bad_feedback.txt'
        with open(file_name, 'a') as f:
            f.write(feedback_entry)

        return jsonify({"message": "Feedback received. Thank You!"})

    except Exception as e:
        return jsonify({"message": f"Error: {str(e)}"}), 500

@app.route("/clear_history", methods=["POST"])
def clear_history():
    session_id = session.get('session_id')
    if session_id and session_id in session_data:
        session_data[session_id] = {'entities': {}, 'history': []}
    return jsonify({"message": "Conversation history cleared."})

if __name__ == "__main__":
    print("FAISS index loaded successfully.")
    print("Starting Flask server on http://127.0.0.1:8000")
    app.run(debug=False, port=8000, use_reloader=False)

