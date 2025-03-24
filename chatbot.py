#Predefined Responses for General Queries
PREDEFINED_RESPONSES = {
    "hi": "Hello! How can I assist you today?",
    "hello": "Hey there! How can I help?",
    "hey": "Hi! How can I assist you?",
    "who are you": "I am a chatbot designed to retrieve data from the database based on your queries.",
    "what can you do": "I can help you fetch information from the database. Try asking things like 'Show all vehicle numbers' or 'Trips in the last 2 months'.",
    "how are you": "I'm just a bot, but I'm here and ready to assist you!",
    "help": "I can help you retrieve database queries. Here are some suggestions:\n- 'How many vehicles entered the plant today?'\n- 'Show me the trips in the last 6 months'\n- 'List all transporters in the database'."
}

import os
import json
import mysql.connector
import requests
import re
import uuid
import faiss
import pickle
from flask import Flask, request, jsonify, make_response, session
from flask_session import Session
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
import logging
import random
 
#Setup Logging
logging.basicConfig(
    filename="query_logs.txt",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

def get_response(user_input):
    return PREDEFINED_RESPONSES.get(user_input.strip().lower(), None)

# def get_response(user_input):
#     return PREDEFINED_RESPONSES.get(user_input, None)

def format_sql_result(sql_result):
    if "error" in sql_result:
        return f"Error: {sql_result['error']}"
    if not sql_result['data']:
        return "No records found."
    response = ""
    columns = sql_result['columns']
    for row in sql_result['data']:
        row_data = ", ".join(f"{col}: {val}" for col, val in zip(columns, row))
        response += row_data + "\n"
    return response

def log_query(query):
    """Log the generated SQL query with a proper tag."""
    try:
        with open("query_logs.txt", "a", encoding="utf-8") as f:
            f.write(f"[SQL] Generated SQL: {query}\n")  # Correct way to write
        print(f"Query logged: {query}")  # Debugging
    except Exception as e:
        print(f"Error writing to query_logs.txt: {e}")
 
def log_error(error_message):
    """Log any database or execution errors with an error tag."""
    logging.error(f"[Error]: {error_message}")
   
def save_session_history():
    """Save session history to a log file when the session ends."""
    if session.get('history'):  # correct, prevents KeyError
        session_id = str(uuid.uuid4())[:8]  # Generate a short session ID
        filename = f"session_logs/session_{session_id}.txt"
 
        os.makedirs("session_logs", exist_ok=True)  # Ensure folder exists
       
        with open(filename, "w") as f:
            for entry in session['history']:
                f.write(f"User: {entry['user']}\n")
                f.write(f"Bot: {entry['bot']}\n\n")
 
        print(f"Session history saved: {filename}")
 
# Load environment variables
load_dotenv(dotenv_path=r'C:\Users\Saksh\chatbot2\.env', override=True)

app = Flask(__name__)
 
# Flask-Session configuration
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Load SentenceTransformer model
embedding_model = SentenceTransformer('sentence-transformers/all-mpnet-base-v2')
 
# Load FAISS Index
try:
    index = faiss.read_index("plant_data.index")
    with open("plant_data.metadata", "rb") as f:
        metadata = pickle.load(f)
    print("FAISS index loaded successfully.")
except Exception as e:
    print(f"Error loading FAISS index: {e}")
    index, metadata = None, None

# Load credentials
MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
LLM_API_ENDPOINT="https://api.groq.com/openai/v1/chat/completions"

print("MYSQL_HOST:", MYSQL_HOST)
print("MYSQL_USER:", MYSQL_USER)
print("MYSQL_DATABASE:", MYSQL_DATABASE)

# Database Schema (Now included)
CACHED_DB_SCHEMA = """
The database 'transactionalplms' has the following structure:

1. transactionalplms.vw_trip_info:
   - id (int): Unique ID of the trip record.
   - tripId (string): Unique trip identifier.
   - plantCode (string): Plant code.
   - plant_name (string): Plant name.
   - movementCode (string): Movement code.
   - TokenNumber (string): Token number for vehicle entry.
   - materialType (string): Type of material.
   - material_code (string): Material code.
   - vehicleNumber (string): Number of the vehicle.
   - chassis_number (string): Chassis number.
   - vehicle_capacity_min, vehicle_capacity_max (float): Vehicle capacity range.
   - vehicle_type (string): Type of vehicle.
   - transporter_name (string): Name of transporter.
   - country_code (string): Country code of vehicle registration.
   - mapPlantStageLocation (string): Current vehicle stage in plant.
   - weightType (string): Weight type.
   - weighmentDate (datetime): Date of weighment.
   - weight (double): Measured weight.
   - isToleranceFailed (boolean): Whether tolerance validation failed.
   - weighbridgeCode (string): Weighbridge code.
   - tolWeightLower, tolWeightUpper (double): Lower and upper weight tolerance.
   - tolerance_Type, minimum_alert, maximum_alert, tolerance_validation (string): Weight tolerance validation details.
   - yardIn, gateIn, gateOut, tareWeight, grossWeight, packingIn, packingOut, unloadingIn, unloadingOut, yardOut, abortedTime (datetime): Timestamps of various plant stages.
   - sealNumber (string): Seal number assigned to the vehicle.
   - tw, gw (double): Tare and gross weights.
   - igpNumber (string): IGP number.
   - driverId (string): Driver ID.
   - abortedRemarks (string): Aborted trip remarks.
   - abortedBy (string): User who aborted the trip.
   - status (char): Status of the trip.
   - dinumber (string): DI number.
   - diqty (double): Quantity associated with DI.
   - ponumber (string): PO number.
   - po_qty (double): PO quantity.
   - consignmentDate (datetime): Consignment date.
   - cityName (string): City name associated with trip.
"""
 
def connect_db():
    """Establish a connection to the MySQL database."""
    try:
        conn = mysql.connector.connect(
            host=MYSQL_HOST,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DATABASE
        )
        return conn
    except mysql.connector.Error as e:
        print(f"Database connection error: {e}")
        return None
 
def execute_sql(query):
    """Execute SQL query and return results as a dictionary."""
    conn = connect_db()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute(query)
            results = cursor.fetchall()
            column_names = [desc[0] for desc in cursor.description]
            cursor.close()
            conn.close()
            return {"columns": column_names, "data": results}  # Corrected return
        except Exception as e:
            print(f"Database query error: {e}")
            return {"columns": [], "data": [], "error": str(e)}
    else:
        print("Database connection failed.")
        return {"columns": [], "data": [], "error": "Database connection failed."}
 
def query_groq_api(prompt):
    """Send a prompt to the Groq API and extract the SQL query."""
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "gemma2-9b-it",
        "messages": [{"role": "user", "content": prompt}]
    }
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        content = response.json()['choices'][0]['message']['content']
        sql_match = re.search(r"```sql\s*(.*?)\s*```", content, re.DOTALL)
        return sql_match.group(1).strip() if sql_match else content.strip()
    except requests.exceptions.RequestException as e:
        print(f"Groq API error: {e}")
        return "Error generating SQL query."

entity_aliases = """
- "vehicle" refers to "vehicleNumber"
- "vehicle number" refers to "vehicleNumber"
- "plant" refers to "plant_name"
- "DI" refers to "dinumber"
- "PO" refers to "ponumber"
- "igp" refers to "igpNumber"
"""


def convert_natural_dates(nl_query):
    """Convert natural language date expressions into SQL-compatible DATE_SUB expressions."""
    # Define common patterns for natural language dates
    patterns = {
        r"\b(last|past) (\d+) days?\b": r"DATE_SUB(NOW(), INTERVAL \2 DAY)",
        r"\b(last|past) (\d+) weeks?\b": r"DATE_SUB(NOW(), INTERVAL \2 WEEK)",
        r"\b(last|past) (\d+) months?\b": r"DATE_SUB(NOW(), INTERVAL \2 MONTH)",
        r"\b(last|past) (\d+) years?\b": r"DATE_SUB(NOW(), INTERVAL \2 YEAR)",
        r"last (\d+) days?": r"DATE_SUB(NOW(), INTERVAL \1 DAY)",
        r"last (\d+) weeks?": r"DATE_SUB(NOW(), INTERVAL \1 WEEK)",
        r"last (\d+) months?": r"DATE_SUB(NOW(), INTERVAL \1 MONTH)",
        r"last (\d+) years?": r"DATE_SUB(NOW(), INTERVAL \1 YEAR)",
        r"from (\d+) days? ago": r"DATE_SUB(NOW(), INTERVAL \1 DAY)",
        r"from (\d+) weeks? ago": r"DATE_SUB(NOW(), INTERVAL \1 WEEK)",
        r"from (\d+) months? ago": r"DATE_SUB(NOW(), INTERVAL \1 MONTH)",
        r"from (\d+) years? ago": r"DATE_SUB(NOW(), INTERVAL \1 YEAR)",
    }
 
    # Replace each pattern in the query with its corresponding SQL syntax
    for pattern, replacement in patterns.items():
        nl_query = re.sub(pattern, replacement, nl_query, flags=re.IGNORECASE)
 
    return nl_query

def generate_sql_from_nl(nl_query, session_history=""):
    """Generate an SQL query from a natural language query using the correct schema."""
    sql_friendly_query = convert_natural_dates(nl_query)

    # Build structured entity context
    entity_context = build_entity_context()

    prompt = f"""
You are an SQL expert using MySQL. Based on the following database schema:

{CACHED_DB_SCHEMA}

**Known Entity Context:**
The following known entity values are available:
{entity_context}

**Entity Aliases:**
{entity_aliases}

**Session History:**
{session_history}

**User Query:**
{sql_friendly_query}

**Instructions:**
- If the user query starts with "how many", "number of", "count of", generate a COUNT query.
- Always map synonyms using the provided entity aliases.
- Always use column names from schema exactly.
- Never use columns like 'vehicle' (wrong), use 'vehicleNumber'.
- Example: For "how many vehicles", use COUNT(DISTINCT vehicleNumber).
- Prioritize entity context when resolving references like 'it' or 'that'.
- If querying column values, select only the required columns.
- Use DISTINCT by default. Add WHERE clauses from entity context if needed.
- Always include database name (transactionalplms.) before table names.
- Use COALESCE(column, 0) for SUM().
Generate a valid MySQL query.
"""
    sql_query = query_groq_api(prompt)
    print(f"Generated SQL Query: {sql_query}")
    if not sql_query:
        return "Error: Could not generate SQL query due to LLM failure"
    
    log_query(sql_query)
    return sql_query


def generate_response_with_llm(user_query, data, column_names):
    """Generate a natural language response using an LLM."""
    prompt = f"""User asked: '{user_query}'.
The retrieved data has the following columns: {', '.join(column_names)}.
The data is: {data}.
Please respond in a concise and natural language format, summarizing the key information for the user."""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gemma2-9b-it",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 200
    }
 
    try:
        response = requests.post(LLM_API_ENDPOINT, headers=headers, json=payload)
        response.raise_for_status()
        content = response.json()['choices'][0]['message']['content']
        return content.strip()
    except requests.exceptions.RequestException as e:
        print(f"LLM API error: {e}")
        return "Error generating natural language response."

def extract_vehicle_number(user_query):
    match = re.search(r'\b[A-Z]{2}\d{2}[A-Z]{2}\d{4}\b', user_query)
    return match.group(0) if match else "the vehicle"

def format_bot_response(column, value):
    # Retrieve column metadata; default to 'Unknown' if column not found
    column_info = COLUMN_METADATA.get(column, {"label": column, "type": "string"})
    label = column_info["label"]
    data_type = column_info["type"]

    # Handle None values gracefully
    if value is None:
        if data_type == "boolean":
            return f"The {label} status is not recorded."
        else:
            return f"The {label} is not recorded."

    # Format response based on data type
    if data_type == "boolean":
        return f"Yes, the {label} has failed." if value else f"No, the {label} has not failed."
    elif data_type == "datetime":
        return f"The {label} is {value.strftime('%Y-%m-%d %H:%M:%S')}."
    elif data_type == "float":
        return f"The {label} is {value:.2f}."
    elif data_type == "int":
        return f"The {label} is {value}."
    else:  # Default case for strings and any unspecified types
        return f"The {label} is {value}."

def generate_natural_response(sql_result, column_names, user_query):
    if not sql_result or not sql_result.get('data'):
        return "No results found."

    data = sql_result['data']

    # Special Case: COUNT Query
    if len(data) == 1 and len(column_names) == 1 and 'count' in column_names[0].lower():
        count_value = data[0][0]
        return f"There are {count_value} records matching your query."

    # Single row, single column (Normal case)
    if len(data) == 1 and len(column_names) == 1:
        formatted_value = format_bot_response(column_names[0], data[0][0])
        return formatted_value

    # Multi-row/multi-column Case
    column_label = COLUMN_METADATA.get(column_names[0], {}).get('label', column_names[0])

    # === If only one column, simplify ===
    if len(column_names) == 1:
        response_lines = [f"Here are the {column_label}s:"]
        for idx, row in enumerate(data, start=1):
            value = row[0] if row[0] is not None else "Not recorded"
            response_lines.append(f"{idx}. {value}")
    else:
        # For multiple columns, keep detailed format
        response_lines = ["Here are the details:"]
        for idx, row in enumerate(data, start=1):
            response_lines.append(f"**{idx}.**")
            for col_name, value in zip(column_names, row):
                formatted_value = format_bot_response(col_name, value)
                response_lines.append(f"- {formatted_value}")
            response_lines.append("")

    return "\n".join(response_lines)

def generate_follow_up_questions(user_query):
    """Generate related follow-up questions based on user query."""
    suggestions_map = {
        "How many vehicles entered the plant today?": [
            "How many vehicles exited the plant today?",
            "Show today's material dispatch details.",
            "Which transporter had the most trips today?"
        ],
        "Show material dispatch details of last month?": [
            "Show material dispatch details for last 6 months.",
            "Which plant dispatched the most material?",
            "How much material was rejected?"
        ],
        "What is the current stage of vehicle ABC123?": [
            "What is the last recorded location of vehicle ABC123?",
            "How long has ABC123 been in the current stage?",
            "Has vehicle ABC123 exited the plant?"
        ],
        "Total trips completed this week?": [
            "Total trips completed last week?",
            "Which transporter completed the most trips?",
            "Show trips completed per day this week."
        ]
    }
    # Convert user query to lowercase for case-insensitive matching
    user_query = user_query.lower()
   
    for key, follow_ups in suggestions_map.items():
        if key in user_query:
            return follow_ups
           
 
    return ["What else can I check?", "Do you need details for a different time period?", "Would you like a summary report?"]

COLUMN_METADATA = {
    "id": {"label": "ID", "type": "int"},
    "tripId": {"label": "Trip ID", "type": "string"},
    "plantCode": {"label": "Plant code", "type": "string"},
    "plant_name": {"label": "Plant name", "type": "string"},
    "movementCode": {"label": "Movement code", "type": "string"},
    "TokenNumber": {"label": "Token number", "type": "string"},
    "materialType": {"label": "Material type", "type": "string"},
    "material_code": {"label": "Material code", "type": "string"},
    "vehicleNumber": {"label": "Vehicle number", "type": "string"},
    "chassis_number": {"label": "Chassis number", "type": "string"},
    "vehicle_capacity_min": {"label": "Vehicle capacity (min)", "type": "float"},
    "vehicle_capacity_max": {"label": "Vehicle capacity (max)", "type": "float"},
    "vehicle_type": {"label": "Vehicle type", "type": "string"},
    "transporter_name": {"label": "Transporter name", "type": "string"},
    "country_code": {"label": "Country code", "type": "string"},
    "mapPlantStageLocation": {"label": "Plant stage location", "type": "string"},
    "weightType": {"label": "Weight type", "type": "string"},
    "weighmentDate": {"label": "Weighment date", "type": "datetime"},
    "weight": {"label": "Weight", "type": "float"},
    "isToleranceFailed": {"label": "Tolerance failed", "type": "boolean"},
    "weighbridgeCode": {"label": "Weighbridge code", "type": "string"},
    "tolWeightLower": {"label": "Lower weight tolerance", "type": "float"},
    "tolWeightUpper": {"label": "Upper weight tolerance", "type": "float"},
    "tolerance_Type": {"label": "Tolerance type", "type": "string"},
    "minimum_alert": {"label": "Minimum alert", "type": "string"},
    "maximum_alert": {"label": "Maximum alert", "type": "string"},
    "tolerance_validation": {"label": "Tolerance validation", "type": "string"},
    "yardIn": {"label": "Yard-in time", "type": "datetime"},
    "gateIn": {"label": "Gate-in time", "type": "datetime"},
    "gateOut": {"label": "Gate-out time", "type": "datetime"},
    "tareWeight": {"label": "Tare weight", "type": "datetime"},
    "grossWeight": {"label": "Gross weight", "type": "datetime"},
    "packingIn": {"label": "Packing-in time", "type": "datetime"},
    "packingOut": {"label": "Packing-out time", "type": "datetime"},
    "unloadingIn": {"label": "Unloading-in time", "type": "datetime"},
    "unloadingOut": {"label": "Unloading-out time", "type": "datetime"},
    "yardOut": {"label": "Yard-out time", "type": "datetime"},
    "abortedTime": {"label": "Aborted time", "type": "datetime"},
    "sealNumber": {"label": "Seal number", "type": "string"},
    "tw": {"label": "Tare weight", "type": "float"},
    "gw": {"label": "Gross weight", "type": "float"},
    "igpNumber": {"label": "IGP number", "type": "string"},
    "driverId": {"label": "Driver ID", "type": "string"},
    "abortedRemarks": {"label": "Aborted remarks", "type": "string"},
    "abortedBy": {"label": "Aborted by", "type": "string"},
    "status": {"label": "Status", "type": "string"},
    "dinumber": {"label": "DI number", "type": "string"},
    "diqty": {"label": "DI quantity", "type": "float"},
    "ponumber": {"label": "PO number", "type": "string"},
    "po_qty": {"label": "PO quantity", "type": "float"},
    "consignmentDate": {"label": "Consignment date", "type": "datetime"},
    "cityName": {"label": "City name", "type": "string"},
}

# Initialize entity store
def initialize_entity_store():
    if 'entities' not in session:
        session['entities'] = {}
    if 'last_entity' not in session:
        session['last_entity'] = None

# Entity patterns
entity_patterns = {
    'tripId': r'trip\s*(?:id|identifier|number)?\s*(?:is|:)?\s*([\w\d\-]+)',
    'plantCode': r'plant\s*code\s*(?:is|:)?\s*([\w\d\-]+)',
    'plant_name': r'plant\s*name\s*(?:is|:)?\s*([\w\d\-]+)',
    'movementCode': r'movement\s*code\s*(?:is|:)?\s*([\w\d\-]+)',
    'TokenNumber': r'token\s*number\s*(?:is|:)?\s*([\w\d\-]+)',
    'materialType': r'material\s*type\s*(?:is|:)?\s*([\w\d\-]+)',
    'material_code': r'material\s*code\s*(?:is|:)?\s*([\w\d\-]+)',
    'vehicleNumber': r'vehicle\s*number\s*(?:is|:)?\s*([\w\d\-]+)',
    'chassis_number': r'chassis\s*number\s*(?:is|:)?\s*([\w\d\-]+)',
    'vehicle_capacity_min': r'vehicle\s*capacity\s*min\s*(?:is|:)?\s*([\d\.]+)',
    'vehicle_capacity_max': r'vehicle\s*capacity\s*max\s*(?:is|:)?\s*([\d\.]+)',
    'vehicle_type': r'vehicle\s*type\s*(?:is|:)?\s*([\w\d\-]+)',
    'transporter_name': r'transporter\s*name\s*(?:is|:)?\s*([\w\d\-]+)',
    'country_code': r'country\s*code\s*(?:is|:)?\s*([\w\d\-]+)',
    'mapPlantStageLocation': r'stage\s*location\s*(?:is|:)?\s*([\w\d\-]+)',
    'yardIn': r'yard\s*in\s*(?:time)?\s*(?:is|:)?\s*([\w\d\-\:]+)',
    'gateIn': r'gate\s*in\s*(?:time)?\s*(?:is|:)?\s*([\w\d\-\:]+)',
    'gateOut': r'gate\s*out\s*(?:time)?\s*(?:is|:)?\s*([\w\d\-\:]+)',
    'tareWeight': r'tare\s*weight\s*(?:time)?\s*(?:is|:)?\s*([\w\d\-\:\s]+)',
    'grossWeight': r'gross\s*weight\s*(?:time)?\s*(?:is|:)?\s*([\w\d\-\:\s]+)',
    'packingIn': r'packing\s*in\s*(?:time)?\s*(?:is|:)?\s*([\w\d\-\:]+)',
    'packingOut': r'packing\s*out\s*(?:time)?\s*(?:is|:)?\s*([\w\d\-\:]+)',
    'unloadingIn': r'unloading\s*in\s*(?:time)?\s*(?:is|:)?\s*([\w\d\-\:]+)',
    'unloadingOut': r'unloading\s*out\s*(?:time)?\s*(?:is|:)?\s*([\w\d\-\:]+)',
    'yardOut': r'yard\s*out\s*(?:time)?\s*(?:is|:)?\s*([\w\d\-\:]+)',
    'abortedTime': r'aborted\s*time\s*(?:is|:)?\s*([\w\d\-\:]+)',
    'weightType': r'weight\s*type\s*(?:is|:)?\s*([\w\d\-]+)',
    'weighmentDate': r'weighment\s*date\s*(?:is|:)?\s*([\w\d\-\:]+)',
    'weight': r'(?:measured\s*)?weight\s*(?:is|:)?\s*([\d\.]+)',
    'isToleranceFailed': r'tolerance\s*(?:failed|status)?\s*(?:is|:)?\s*(true|false)',
    'weighbridgeCode': r'weighbridge\s*code\s*(?:is|:)?\s*([\w\d\-]+)',
    'tolWeightLower': r'lower\s*tolerance\s*(?:weight)?\s*(?:is|:)?\s*([\d\.]+)',
    'tolWeightUpper': r'upper\s*tolerance\s*(?:weight)?\s*(?:is|:)?\s*([\d\.]+)',
    'tolerance_Type': r'tolerance\s*type\s*(?:is|:)?\s*([\w\d\-]+)',
    'minimum_alert': r'minimum\s*alert\s*(?:is|:)?\s*([\w\d\-]+)',
    'maximum_alert': r'maximum\s*alert\s*(?:is|:)?\s*([\w\d\-]+)',
    'tolerance_validation': r'tolerance\s*validation\s*(?:is|:)?\s*([\w\d\-]+)',
    'sealNumber': r'seal\s*number\s*(?:is|:)?\s*([\w\d\-]+)',
    'tw': r'tare\s*weight\s*(?:is|:)?\s*([\d\.]+)',
    'gw': r'gross\s*weight\s*(?:is|:)?\s*([\d\.]+)',
    'igpNumber': r'igp\s*number\s*(?:is|:)?\s*([\w\d\-]+)',
    'driverId': r'driver\s*id\s*(?:is|:)?\s*([\w\d\-]+)',
    'abortedRemarks': r'aborted\s*remarks\s*(?:is|:)?\s*([\w\d\s\-]+)',
    'abortedBy': r'aborted\s*by\s*(?:is|:)?\s*([\w\d\-]+)',
    'status': r'status\s*(?:is|:)?\s*([\w\d\-]+)',
    'dinumber': r'di\s*number\s*(?:is|:)?\s*([\w\d\-]+)',
    'diqty': r'di\s*quantity\s*(?:is|:)?\s*([\d\.]+)',
    'ponumber': r'po\s*number\s*(?:is|:)?\s*([\w\d\-]+)',
    'po_qty': r'po\s*quantity\s*(?:is|:)?\s*([\d\.]+)',
    'consignmentDate': r'consignment\s*date\s*(?:is|:)?\s*([\w\d\-]+)',
    'cityName': r'city\s*name\s*(?:is|:)?\s*([\w\d\s\-]+)',
}

def extract_entities(user_message):
    initialize_entity_store()
    
    session_id = session.get('session_id')  # Get session_id at the top!

    # Flag to check if entity found in this query
    entity_found = False
    
    for entity, pattern in entity_patterns.items():
        match = re.search(pattern, user_message, re.IGNORECASE)
        if match:
            value = match.group(1)
            session_data[session_id]['entities'][entity] = value
            session_data[session_id]['last_entity'] = entity
            entity_found = True
    
    # If no explicit entity found, check for pronouns (contextual reference)
    if not entity_found:
        # Replace pronouns like 'that', 'it' with last known entity value
        if session_id and session_data[session_id].get('last_entity'):
            ref_entity = session_data[session_id]['last_entity']
            ref_value = session_data[session_id]['entities'].get(ref_entity, "")
            if ref_value:
                user_message = re.sub(r'\b(that|it)\b', ref_value, user_message, flags=re.IGNORECASE)
    
    # Log the current entity store (for debugging)
    print("Entity Store:", session_data[session_id]['entities'])
    
    return user_message

def generate_response(user_message):
    # Detect if there's a context switch
    context_switched = detect_context_switch(user_message)
    entities = get_session_entities()
    
    # Generate a response based on the current context
    if 'vehicleNumber' in entities:
        vehicle_number = entities['vehicleNumber']
        # Example response incorporating the vehicle number
        bot_response = f"The details for vehicle {vehicle_number} are as follows..."
    else:
        bot_response = "I'm sorry, I don't have enough information. Could you please provide more details?"

    # Update conversation history
    update_conversation_history(user_message, bot_response)
    return bot_response

def build_entity_context():
    initialize_entity_store()
    
    session_id = session.get('session_id')  # Get session_id first
    
    entity_context_lines = []
    for key, value in session_data[session_id]['entities'].items():
        label = COLUMN_METADATA.get(key, {}).get('label', key)
        entity_context_lines.append(f"The {label} is {value}.")
    
    return "\n".join(entity_context_lines)

# Initialize a global dictionary to store session data
session_data = {}

def get_session_entities():
    return session_data[session['session_id']]['entities']

def update_session_entities(entity, value):
    session_data[session['session_id']]['entities'][entity] = value

def detect_context_switch(user_message):
    # Define a pattern to detect vehicle numbers (e.g., 'MP04HE4034')
    vehicle_pattern = r'\b[A-Z]{2}\d{2}[A-Z]{2}\d{4}\b'
    match = re.search(vehicle_pattern, user_message)
    if match:
        vehicle_number = match.group(0)
        entities = get_session_entities()
        # Check if the detected vehicle number differs from the current context
        if entities.get('vehicleNumber') != vehicle_number:
            # Reset entity store for new context
            session_data[session['session_id']]['entities'] = {'vehicleNumber': vehicle_number}
            session_data[session['session_id']]['last_entity']='vehicleNumber'
            return True
    return False

def update_conversation_history(user_message, bot_response):
    session_data[session['session_id']]['history'].append({'user': user_message, 'bot': bot_response})

def get_conversation_history():
    return session_data[session['session_id']]['history']

def get_bot_response(user_message):
    try:
        # Always ensure session_id & session_data initialized
        session_id = session.get('session_id')
        if not session_id:
            session_id = str(uuid.uuid4())
            session['session_id'] = session_id
        if session_id not in session_data:
            session_data[session_id] = {'entities': {}, 'history': []}

        # Check for predefined response
        predefined_reply = get_response(user_message.lower())
        if predefined_reply:
            session_data[session_id]['history'].append({
                "user": user_message,
                "bot": predefined_reply
            })
            return predefined_reply

        # Initialize entity store
        initialize_entity_store()

        # Extract entities dynamically
        modified_message = extract_entities(user_message)

        # Retrieve session-based history
        history_entries = session_data[session_id]['history']
        session_history = "\n".join([
            f"User: {entry['user']} | Bot: {entry['bot']}"
            for entry in history_entries
        ])

        # Build entity context
        entity_context = build_entity_context()
        combined_context = f"{session_history}\n\nEntity Context:\n{entity_context}"

        # Generate SQL query
        sql_query = generate_sql_from_nl(modified_message, session_history=combined_context)

        # Execute SQL query
        sql_result = execute_sql(sql_query)

        if 'error' in sql_result:
            response = f"Error executing query: {sql_result['error']}"
        else:
            data = sql_result.get('data', [])
            columns = sql_result.get('columns', [])

            if not data:
                response = "I couldn't find any data matching your query."
            else:
                unique_data = [list(row) for row in set(tuple(r) for r in data)]
                cleaned_result = sql_result.copy()
                cleaned_result['data'] = unique_data

                # Generate natural response
                response = generate_natural_response(cleaned_result, columns, modified_message)

        # Update history
        session_data[session_id]['history'].append({
            "user": user_message,
            "bot": response
        })

        return response

    except Exception as e:
        log_error(f"Exception in get_bot_response: {str(e)}")
        return f"Sorry, something went wrong. {str(e)}"

@app.route('/chat', methods=['POST'])
def chat():
    """Chat endpoint to process user queries with session history."""
    user_query = request.json.get('query', '').strip()
    if not user_query:
        return jsonify({"response": "Please ask a valid question."})
 
    #Ensure session history exists
    if 'history' not in session:
        session['history'] = []
 
    #Normalize user query for case-insensitive matching
    user_query_lower = user_query.lower()
 
    #Convert predefined responses to lowercase
    PREDEFINED_RESPONSES_LOWER = {k.lower(): v for k, v in PREDEFINED_RESPONSES.items()}
    #Check for predefined responses
    if user_query_lower in PREDEFINED_RESPONSES_LOWER:
        response_text = PREDEFINED_RESPONSES_LOWER[user_query_lower]  # Corrected key
        session['history'].append({"user": user_query, "bot": response_text})
        session.modified = True  # Ensure session updates are saved
        return make_response(jsonify({"response": response_text}))
 
    #Retrieve session history and format it for context
    past_conversations = "\n".join([f"User: {entry['user']}\nBot: {entry['bot']}" for entry in session['history']])
 
    #Modify query to include session history
    sql_query = generate_sql_from_nl(user_query, past_conversations)
    sql_result = execute_sql(sql_query)  
 
    print(f"DEBUG: sql_result = {sql_result}")
 
    column_names = sql_result.get('columns', [])
    data = sql_result.get('data', [])
    error = sql_result.get('error')
 
    if error:
        return make_response(jsonify({"response": f"Database Error: {error}"}))
 
    if not column_names or not data:
        return make_response(jsonify({"response": "No results found or error in query."}))
 
    response_text = generate_natural_response({'columns': column_names, 'data': data}, column_names) # modified to pass the correct dictionary
 
    print(f"DEBUG: response_text = {response_text}")  # Debugging line
 
    # Generate related questions
    suggested_questions = generate_follow_up_questions(user_query)
 
    # Store conversation in session history
    session['history'].append({"user": user_query, "bot": response_text})
    session.modified = True  # Ensure session updates are saved
 
    print(f"Follow-up questions generated: {suggested_questions}")  # Debugging log
 
    return make_response(jsonify({"response": response_text, "suggestions": suggested_questions}))

@app.route("/feedback", methods=["POST"])
def feedback():
    data = request.get_json()
    user_query = data.get("query")
    bot_response = data.get("response")
    feedback = data.get("feedback")  # "like" or "dislike"
    
    feedback_entry = f"User Query: {user_query}\nBot Response: {bot_response}\nFeedback: {feedback}\n\n"
    
    if feedback == "like":
        with open("good_feedback.txt", "a") as f:
            f.write(feedback_entry)
    elif feedback == "dislike":
        with open("bad_feedback.txt", "a") as f:
            f.write(feedback_entry)
    else:
        return jsonify({"message": "Invalid feedback type."}), 400
    
    return jsonify({"message": "Feedback saved successfully!"})


@app.route('/end_session', methods=['POST'])
def end_session():
    """Endpoint to save and clear session history when the session ends."""
    save_session_history()  # Save chat history to a log file
    session.clear()  # Clear session data
    session.modified = True  # Ensure session updates are recognized
    return jsonify({"message": "Session ended, history saved."})
 
# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

if __name__ == '__main__':
    app.run(debug=True)
 
