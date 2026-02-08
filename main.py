from flask import Flask, request, jsonify, session
import uuid
from datetime import timedelta, datetime, timezone
import logging
import os
from flask_cors import CORS
import requests
import mysql.connector
import re
import json
from sqlgen import generate_sql_from_nl, execute_sql, get_response, extract_plant_from_query
from nlgen import generate_natural_language_response

app = Flask(__name__)
CORS(app)

app.secret_key = "your_secret_key"
app.permanent_session_lifetime = timedelta(minutes=30)

# Set up logging (existing)
log_directory = "chat_logs"
os.makedirs(log_directory, exist_ok=True)
log_file = os.path.join(log_directory, "chat_history.log")

logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format='%(asctime)s - %(session_id)s - USER: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# --- NEW: JSON Logging Setup ---
JSON_LOG_FILE = "query_logs.jsonl"  # Separate log for structured data
# --- End of JSON Logging Setup ---

# Global session_data (existing)
session_data = {}

def get_session():
    """Gets or initializes the user session."""
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
        session_data[session['session_id']] = {
            "entities": {},
            "history": []
        }

    session_id = session['session_id']
    current_session = session_data.get(session_id)

    if current_session is None:
        current_session = {
            "entities": {},
            "history": []
        }
        session_data[session_id] = current_session

    return session_id, current_session

@app.before_request
def before_request():
    """Ensure session is initialized before processing any request."""
    get_session()

def extract_vehicle_number(user_query):
    match = re.search(r'\b[A-Z]{2}\d{2}[A-Z]{2}\d{4}\b', user_query)
    return match.group(0) if match else "the vehicle"

# --- NEW:  JSON Logging Function ---
def log_query_json(user_query, sql_query, bot_response, error=None, feedback=None):
    """Logs query details to a JSON file."""
    try:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_query": user_query,
            "sql_query": sql_query,
            "bot_response": bot_response,
            "error": str(error) if error else None,
            "session_id": session.get('session_id'),
            "plant_code": session.get('plant_code'),
            "feedback": feedback  # Added feedback field
        }
        with open(JSON_LOG_FILE, "a") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception as e:
        logging.error(f"JSON Log Error: {e}")
# --- End of JSON Logging Function ---

@app.route("/chat", methods=["POST"])
def chat():
    """Handles user queries, generates SQL, executes it, and generates a natural language response."""

    data = request.get_json()
    user_query = data.get("query")
    plant_code = data.get("plantCode")

    session_id, current_session = get_session()

    # Update plant code if provided
    if plant_code:
        session['plant_code'] = plant_code
        print(f"plant_code set in session: {session['plant_code']}")
    else:
        plant_code = session.get('plant_code')
        print(f"plant_code retrieved from session: {plant_code}")

    if not plant_code:
        return jsonify({"response": "Error: Plant code must be provided."}), 400

    vehicle_number = extract_vehicle_number(user_query)
    if vehicle_number:
        current_session['entities']['vehicle_number'] = vehicle_number

    logging.info(f"\n==== New Chat ====\nUser: {user_query}")

    if not user_query:
        return jsonify({"response": "Please enter a valid question."}), 400

    predefined_reply = get_response(user_query.lower())
    if predefined_reply:
        current_session['history'].append({"user": user_query, "bot": predefined_reply})
        logging.info(f"Bot: {predefined_reply}")
        log_query_json(user_query, "N/A", predefined_reply) # JSON Log for predefined reply
        return jsonify({"response": predefined_reply, "query": user_query})

    queried_plant_code, queried_plant_name = extract_plant_from_query(user_query)

    if queried_plant_code:
        if queried_plant_code != session.get('plant_code'):
            return jsonify({
                "response": "Oops! It looks like you're trying to access information from a plant you're not authorized to. Please check the plant you're trying to query or contact support if you think there's a mistake."
            }), 200
        else:
            session['plant_code'] = queried_plant_code
            print(f"plant_code updated in session: {session['plant_code']}")
    else:
        plant_code = session.get('plant_code')
        print(f"plant_code retrieved from session: {plant_code}")

    try:
        sql_query = generate_sql_from_nl(user_query, plant_code=plant_code)

        print(f"SQL Query from generate_sql_from_nl: {sql_query}")

        if sql_query.strip().lower().startswith("sorry") or "could you please clarify" in sql_query.lower():
            log_query_json(user_query, "N/A", sql_query) # JSON Log for clarification/sorry
            return jsonify({"response": sql_query, "query": user_query}), 200

        if isinstance(sql_query, dict) and "error" in sql_query:
            logging.error(f"SQL Generation Error: {sql_query['error']}")
            log_query_json(user_query, "N/A", "Error in SQL generation", error=sql_query['error'])  # JSON Log
            return jsonify({"response": "Sorry, I could not understand your query.", "query": user_query}), 200

        sql_result = execute_sql(sql_query, plant_code=plant_code)
        if "error" in sql_result:
            logging.error(f"SQL Execution Error: {sql_result['error']}")
            log_query_json(user_query, sql_query, "Error in SQL execution", error=sql_result['error'])  # JSON Log
            return jsonify(
                {"response": "Sorry, I encountered an error while querying the database.", "query": user_query}), 500

        nl_response = generate_natural_language_response(sql_result, user_query)
        if "error" in nl_response:
            logging.error(f"NLG Error: {nl_response['error']}")
            log_query_json(user_query, sql_query, "Error in NL generation", error=nl_response['error'])  # JSON Log
            return jsonify({"response": "Sorry, I could not generate a response.", "query": user_query}), 500

        current_session['history'].append({"user": user_query, "bot": nl_response})
        logging.info(f"Bot: {nl_response}")
        log_query_json(user_query, sql_query, nl_response)  # JSON Log (Success)
        return jsonify({"response": nl_response, "query": user_query})

    except mysql.connector.Error as db_error:
        logging.error(f"Database error: {str(db_error)}")
        log_query_json(user_query, "N/A", "Database Connection Error", error=str(db_error))  # JSON Log
        return jsonify({"response": "Sorry, I'm having trouble connecting to the database. Please try again later.",
                        "query": user_query}), 500
    except requests.exceptions.RequestException as api_error:
        logging.error(f"LLM API error: {str(api_error)}")
        log_query_json(user_query, "N/A", "LLM API Error", error=str(api_error))  # JSON Log
        return jsonify(
            {"response": "Sorry, I'm unable to process your request due to an API issue. Please try again later.",
             "query": user_query}), 500
    except Exception as e:
        logging.exception("An unexpected error occurred: ", exc_info=True)
        log_query_json(user_query, "N/A", "Unexpected Error", error=str(e))  # JSON Log
        return jsonify({"response": "Sorry, I cannot process your query at the moment. Please try again later.",
                        "query": user_query}), 500

@app.route("/feedback", methods=["POST"])
def feedback():
    """Handles user feedback on bot responses."""
    data = request.get_json()
    user_query = data.get("query")
    bot_response = data.get("response")
    feedback_type = data.get("feedback")

    if not user_query or not bot_response or feedback_type not in [0, 1]:
        return jsonify({"message": "Incomplete feedback data."}), 400

    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        feedback_entry = (
            f"Timestamp: {timestamp}\n"
            f"User Query: {user_query}\n"
            f"Bot Response:\n{bot_response}\n"
            f"Feedback: {'good' if feedback_type == 1 else 'bad'}\n"
            f"{'-' * 50}\n"
        )

        file_name = 'good_feedback.txt' if feedback_type == 1 else 'bad_feedback.txt'
        with open(file_name, 'a') as f:
            f.write(feedback_entry)

        # --- Log feedback to JSON log ---
        log_query_json(user_query, None, bot_response, feedback={'type': 'good' if feedback_type == 1 else 'bad'})
        # --- End of feedback logging ---

        return jsonify({"message": "Feedback received. Thank You!"})

    except Exception as e:
        return jsonify({"message": f"Error: {str(e)}"}), 500

@app.route("/clear_history", methods=["POST"])
def clear_history():
    """Clears the conversation history for the current session."""
    session_id, _ = get_session()
    session_data[session_id] = {'entities': {}, 'history': []}
    return jsonify({"message": "Conversation history cleared."}), 200

if __name__ == "__main__":
    print("Starting Flask server on http://0.0.0.0:8000")
    app.run(debug=True, port=8000, use_reloader=False, host='0.0.0.0')