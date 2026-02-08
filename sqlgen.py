
import os
import json
import mysql.connector
import requests
import re
import uuid
from flask import Flask, request, jsonify,session
from flask_session import Session
from dotenv import load_dotenv
import logging
from threading import Lock

# Setup Logging
# logging.basicConfig(
#     filename="query_logs.txt",
#     level=logging.INFO,
#     format="%(asctime)s - %(levelname)s - %(message)s",
# )

# Restricted SQL keywords for injection prevention
RESTRICTED_SQL_KEYWORDS = [
    "delete", "update", "insert", "truncate", "drop", "alter", "create",
    "replace", "grant", "revoke", "execute", "call", "--", ";", "/*", "*/",
    "union", "into", "load", "outfile", "dumpfile", "shutdown", "lock"
]

# Load predefined responses from JSON file
try:
    with open("predefined_responses.json", "r") as f:
        PREDEFINED_RESPONSES = json.load(f)
except FileNotFoundError:
    print("Error: 'predefined_responses.json' not found.  Using empty dict.")
    logging.error("Error: 'predefined_responses.json' not found.")
    PREDEFINED_RESPONSES = {}
except json.JSONDecodeError as e:
    print(f"Error: Invalid JSON in 'predefined_responses.json': {e}")
    logging.error(f"Error: Invalid JSON in 'predefined_responses.json': {e}")
    PREDEFINED_RESPONSES = {}  # Ensure it's initialized to an empty dict to prevent errors later.

# Sample plant mapping
PLANT_NAME_CODE_MAP = {
    "maratha": "NE03",
    "sindri": "N205",
    "nalagarh": "N225",
    "rajpura": "NT45",
    "panvel": "NE25"
}

# reverse mapping for code lookup
PLANT_CODE_NAME_MAP = {v.lower(): k for k, v in PLANT_NAME_CODE_MAP.items()}

def get_response(user_input):
    """
    Retrieves a predefined response for a given user input.

    Args:
        user_input (str): The user input.

    Returns:
        str: The predefined response, or None if not found.
    """
    return PREDEFINED_RESPONSES.get(user_input.strip().lower(), None)

def extract_plant_from_query(query):
    query = query.lower()

    # Check for plant name in query
    for plant_name, code in PLANT_NAME_CODE_MAP.items():
        if plant_name in query:
            return code, plant_name

    # Check for plant code in query (e.g., N205)
    for code in PLANT_CODE_NAME_MAP:
        if code in query:
            return code, PLANT_CODE_NAME_MAP[code]

    return None, None

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
    try:
        with open("query_logs.txt", "a", encoding="utf-8") as f:
            f.write(f"[SQL] Generated SQL: {query}\n")
        print(f"Query logged: {query}")
    except Exception as e:
        print(f"Error writing to query_logs.txt: {e}")

def log_error(error_message):
    logging.error(f"[Error]: {error_message}")

def save_session_history():
    if session.get('history'):
        session_id = str(uuid.uuid4())[:8]
        filename = f"session_logs/session_{session_id}.txt"
        os.makedirs("session_logs", exist_ok=True)
        with open(filename, "w") as f:
            for entry in session['history']:
                f.write(f"User: {entry['user']}\n")
                f.write(f"Bot: {entry['bot']}\n\n")
        print(f"Session history saved: {filename}")

# Load environment variables
load_dotenv()
app = Flask(__name__)

# Flask-Session configuration
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
app.secret_key = os.getenv("SECRET_KEY", "your-secret-key-here")
Session(app)

session_lock = Lock()

# Load credentials
MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE")
SQLGEN_GROQ_API_KEY = os.getenv("SQLGEN_GROQ_API_KEY")
LLM_API_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

# Database Schema
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
   - mapPlantStageLocation (string): Current location and stage of vehicle at plant.
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
        logging.error(f"Database connection error: {e}")
        return None


def validate_sql_query(query):
    """Validate SQL syntax before execution."""
    required_clauses = ["SELECT", "FROM"]

    for clause in required_clauses:
        if clause not in query.upper():
            return False, f"Missing SQL clause: {clause}"

    # Check for balanced parentheses
    if query.count('(') != query.count(')'):
        return False, "Unbalanced parentheses in SQL query."

    # Validate WHERE clause format
    if "WHERE" in query.upper():
        if not re.search(r'\b\w+\s*(=|IN|LIKE|BETWEEN|>|<|>=|<=)\s*[\w\'"\(\)]+', query, re.IGNORECASE):
            return False, "WHERE clause must contain a valid condition."

    return True, "Valid SQL query."

def fix_generated_sql(query, plant_code=None):
    """Fix SQL query formatting issues and ensure plantCode is added or corrected."""

    # Normalize query
    query = query.strip().rstrip(';')

    # Fix incorrect DISTINCT placement
    query = re.sub(r'SELECT\s+(\w+),\s*DISTINCT', r'SELECT DISTINCT \1,', query, flags=re.IGNORECASE)

    # Fix incorrect WHERE AND
    query = re.sub(r"\bWHERE\s+AND\b", "WHERE", query, flags=re.IGNORECASE)

    # Check if plant_code is None
    if plant_code is None:
        raise ValueError("plant_code must be provided")

    # If plant_code is provided, proceed with fixing the query
    if plant_code:
        # Replace existing plantCode condition
        query = re.sub(r"plant[_]?code\s*=\s*'[^']*'", f"plantCode = '{plant_code}'", query, flags=re.IGNORECASE)

        # If plantCode is still not in query, add it
        if not re.search(r"\bplant[_]?code\b", query, flags=re.IGNORECASE):
            if "where" in query.lower():
                query = re.sub(r"(where\s+)", f"\\1plantCode = '{plant_code}' AND ", query, flags=re.IGNORECASE)
            elif "limit" in query.lower():
                query = re.sub(r"(limit\s+\d+)", f"WHERE plantCode = '{plant_code}' \\1", query, flags=re.IGNORECASE)
            else:
                query += f" WHERE plantCode = '{plant_code}'"

    return query

def execute_sql(query, plant_code=None):
    """
    Executes an SQL query against the database.

    Args:
        query (str): The SQL query to execute.
        plant_code (str, optional): The plant code to filter the query. Defaults to None.

    Returns:
        dict: A dictionary containing the column names and data, or an error message.
              Expected keys: 'columns' (list), 'data' (list of lists), or 'error' (str).
    """
    # Fix SQL format issues and enforce plant code
    query = fix_generated_sql(query, plant_code)

    # Validate SQL
    is_valid, msg = validate_sql_query(query)
    if not is_valid:
        error_message = f"SQL Validation Failed: {msg} for query: {query}"
        print(error_message)
        logging.error(error_message)
        return {"error": error_message}  # Return structured error

    try:
        conn = connect_db()
        if conn is None:  # Check if connection failed
            error_message = "Database connection failed."
            print(error_message)
            logging.error(error_message)
            return {"error": error_message}  # Return structured error

        cursor = conn.cursor()
        cursor.execute(query)
        results = cursor.fetchall()
        column_names = [desc[0] for desc in cursor.description]

        cursor.close()
        conn.close()

        return {"columns": column_names, "data": results}  # Return structured data

    except mysql.connector.Error as e:
        error_message = f"Database query error: {e} for query: {query}"
        print(error_message)
        logging.error(error_message)
        return {"error": error_message}  # Return structured error
    except Exception as e:
        error_message = f"Unexpected error executing SQL: {e} for query: {query}"
        print(error_message)
        logging.error(error_message)
        return {"error": "Internal server error"}  # Return structured error

def query_groq_api(prompt):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {SQLGEN_GROQ_API_KEY}",
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
- "vehicles" refers to "vehicleNumber"
- "truck" refers to "vehicleNumber"
- "truck number" refers to "vehicleNumber"

- "truck no" refers to "vehicleNumber"
- "truck no." refers to "vehicleNumber"
- "lorry" refers to "vehicleNumber"
- "lorry number" refers to "vehicleNumber"
- "lorry no" refers to "vehicleNumber"
- "lorry no." refers to "vehicleNumber"
- "registration number" refers to "vehicleNumber"
- "reg number" refers to "vehicleNumber"
- "reg no" refers to "vehicleNumber"
- "plate number" refers to "vehicleNumber"

- "tare weight" refers to "tw"
- "gross weight" refers to "gw"
- "tareweight" refers to "tw"
- "grossweight" refers to "gw"
- "tareweight time" refers to "tareWeight"
- "grossweight time" refers to "grossWeight"
- "time of tareweight" refers to "tareWeight"
- "time of grossweight" refers to "grossWeight"

- "plant" refers to "plant_name"
- "plant name" refers to "plant_name"
- "facility" refers to "plant_name"
- "site" refers to "plant_name"

- "plant code" refers to "plantCode"
- "plant id" refers to "plantCode"
- "facility code" refers to "plantCode"
- "site code" refers to "plantCode"

- "is tolerance failed" refers to "isToleranceFailed"
- "tolerance failed" refers to "isToleranceFailed"
- "tolerance" refers to "tolerance_validation"

- "DI" refers to "dinumber"
- "delivery instruction" refers to "dinumber"
- "di number" refers to "dinumber"
- "delivery no" refers to "dinumber"

- "PO" refers to "ponumber"
- "purchase order" refers to "ponumber"
- "po number" refers to "ponumber"
- "order number" refers to "ponumber"
- "order no" refers to "ponumber"

- "igp" refers to "igpNumber"
- "igp number" refers to "igpNumber"
- "inward gate pass" refers to "igpNumber"
- "gate pass" refers to "igpNumber"
- "entry pass" refers to "igpNumber"

- "material" refers to "materialType"
- "material type" refers to "materialType"

- "material code" refers to "material_code"
- "material id" refers to "material_code"

- "transporter" refers to "transporter_name"
- "transport company" refers to "transporter_name"
- "carrier" refers to "transporter_name"
- "shipping company" refers to "transporter_name"

- "stage" refers to "mapPlantStageLocation"
- "current stage" refers to "mapPlantStageLocation"
- "position" refers to "mapPlantStageLocation"

- "weight" refers to "weight"
- "measured weight" refers to "weight"
- "load weight" refers to "weight"

- "driver" refers to "driverId"
- "driver id" refers to "driverId"
- "driver number" refers to "driverId"

- "token" refers to "TokenNumber"
- "token number" refers to "TokenNumber"
- "entry token" refers to "TokenNumber"

- "trip" refers to "tripId"
- "trip id" refers to "tripId"
- "trip number" refers to "tripId"
"""

def convert_natural_dates(nl_query):
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
    for pattern, replacement in patterns.items():
        nl_query = re.sub(pattern, replacement, nl_query, flags=re.IGNORECASE)
    return nl_query

def is_safe_sql_query(sql_query):
    """Check if the SQL query is safe (only SELECT statements allowed)."""
    sql_lower = sql_query.lower().strip()
    print(f"Checking SQL safety: {sql_query}")  # Debug

    # Check if it starts with SELECT
    if not sql_lower.startswith("select"):
        print("Rejected: Does not start with SELECT")  # Debug
        return False, "Query rejected due to security reason(s): Contains restricted keywords, consider rephrasing your query."

    RESTRICTED_SQL_KEYWORDS = [
        "delete", "update", "insert", "truncate", "drop", "alter", "create",
        "replace", "grant", "revoke", "execute", "call", "--", ";", r"/\*", r"\*/",
        "union", "into", "load", "outfile", "dumpfile", "shutdown", "lock", "set"
    ]

    for keyword in RESTRICTED_SQL_KEYWORDS:
        escaped_keyword = re.escape(keyword)
        if re.search(rf"\b{escaped_keyword}\b", sql_lower):
            print(f"Rejected: Contains restricted keyword '{keyword}'")  # Debug
            return False, f" Your query contains a restricted SQL keyword: '{keyword}'. SQL injection is not permitted."

    if ";" in sql_lower and not sql_lower.endswith(";"):
        print("Rejected: Contains semicolon not at end")  # Debug
        return False, " Multiple SQL statements are not allowed. Please submit only one SELECT query."

    print("SQL query is safe")  # Debug
    return True, ""

def is_plant_related_query(query):
    """
    Checks if the given query is related to plant data using the Groq API.
    """
    prompt = f"""
    Is the following query related to plant data, plant operations, vehicles in a plant, or any information that might be found in a plant database? Answer "yes" or "no".
    Query: {query}
    """

    headers = {
        "Authorization": f"Bearer {SQLGEN_GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gemma2-9b-it",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,  # Lower temperature for more deterministic output
        "max_tokens": 10
    }
    try:
        response = requests.post(LLM_API_ENDPOINT, headers=headers, json=payload)
        response.raise_for_status()
        content = response.json()['choices'][0]['message']['content'].strip().lower()
        return "yes" in content
    except requests.exceptions.RequestException as e:
        print(f"LLM API Error in is_plant_related_query: {e}")
        logging.error(f"LLM API error in is_plant_related_query: {e}")
        return jsonify(
            {"response": "Sorry, I'm unable to process your request due to an API issue. Please try again later."})

def validate_timestamps(start_time, end_time):
    """Ensure start timestamp is earlier than the end timestamp."""
    if start_time > end_time:
        return False, f"Invalid timestamps: {start_time} should be earlier than {end_time}"
    return True, "Valid timestamps."

VALID_TIMESTAMP_COLUMNS = {
    "yardIn", "gateIn", "gateOut", "tareWeight", "grossWeight",
    "packingIn", "packingOut", "unloadingIn", "unloadingOut",
    "yardOut", "abortedTime"
}

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

def initialize_entity_store():
    if 'entities' not in session:
        session['entities'] = {}
    if 'last_entity' not in session:
        session['last_entity'] = None
    session_id = session.get('session_id')
    if session_id and session_id not in session_data:
        session_data[session_id] = {'entities': {}, 'history': [], 'entity_history': []}

def build_entity_context():
    initialize_entity_store()
    session_id = session.get('session_id')
    if session_id not in session_data:
        session_data[session_id] = {'entities': {}, 'history': []}
    entity_context_lines = []
    for key, value in session_data[session_id]['entities'].items():
        label = COLUMN_METADATA.get(key, {}).get('label', key)
        entity_context_lines.append(f"The {label} is {value}.")
    return "\n".join(entity_context_lines)

def is_gibberish(query):
    """Check if the query is random gibberish (non-sensible input)."""
    # Check if the query contains mostly non-alphabetic characters (i.e., random gibberish)
    if len(re.findall(r'[^a-zA-Z0-9\s]', query)) > 0.8 * len(query):  # More than 80% non-alphanumeric
        return True
    # Check if the query has very few meaningful words or is just random
    if len(query.split()) < 2:
        return True
    return False

# Initialize a global dictionary to store session data
session_data = {}

# Setup Logging
logging.basicConfig(
    filename="sql_query_logs.txt",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

BOOLEAN_KEYWORDS = ["is", "are", "does", "can", "whether", "if", "has", "have"]

def is_boolean_query(query):
    first_word = query.split()[0].lower() if query.split() else ""
    return first_word in BOOLEAN_KEYWORDS


BOOLEAN_LLM_INSTRUCTIONS = """
If the user query is a yes/no (boolean) question (e.g., starting with "Is", "Are", "Does", "Has", "Whether"),
you MUST generate a SQL query in the format:
SELECT CASE
    WHEN EXISTS (
        -- Generate the inner SELECT statement with conditions based ONLY on explicitly requested columns
        SELECT 1
        FROM transactionalplms.vw_trip_info t
        WHERE -- Your relevant conditions for the boolean check, e.g., t.vehicleNumber = '6JSD9Y9' AND t.materialType = 'PPC'
    )
    THEN 'yes'
    ELSE 'no'
END AS result;
Ensure you only include conditions directly related to the user's boolean question. Do NOT include any columns or conditions that are not explicitly asked for by the user. Prioritize identifying specific entities (like vehicle numbers, material types) and generating exact match conditions.
"""


def generate_sql_from_nl(nl_query, session_history="", plant_code=None):
    """Generate an SQL query from a natural language query using the correct schema."""

    if is_gibberish(nl_query):
        print("Detected gibberish:", nl_query)
        return "Sorry, I didn't understand your request. Could you please clarify?"

    # if is_boolean_query(nl_query):
    #     boolean_sql = generate_boolean_sql(nl_query, plant_code, CACHED_DB_SCHEMA, COLUMN_METADATA)
    #     if boolean_sql:
    #         print(f"Generated Boolean SQL Query: {boolean_sql}")
    #         return boolean_sql
    #     else:
    #         return "Could not generate specific boolean SQL for this query."

    sql_friendly_query = convert_natural_dates(nl_query)

    # Build structured entity context
    entity_context = build_entity_context()

    # Detect multiple vehicle numbers
    vehicle_pattern = r'\b[A-Z]{2}\d{2}[A-Z]{0,2}\d{4}\b'
    vehicle_numbers = re.findall(vehicle_pattern, nl_query, re.IGNORECASE)

    # Extract timestamp columns from query
    found_timestamps = [col for col in VALID_TIMESTAMP_COLUMNS if col in nl_query]

    # Ensure exactly 2 timestamps are present for TAT calculation
    if len(found_timestamps) == 2:
        start_time, end_time = found_timestamps
        tat_sql = f"""
            TIMESTAMPDIFF(
                MINUTE, 
                LEAST(CAST(t.{start_time} AS DATETIME), CAST(t.{end_time} AS DATETIME)), 
                GREATEST(CAST(t.{start_time} AS DATETIME), CAST(t.{end_time} AS DATETIME))
            ) AS TAT
        """
    else:
        tat_sql = ""

    prompt = f"""
    You are an AI assistant tasked with converting natural language questions into SQL queries.
    Use the following database schema to generate accurate SQL statements:
    You are an SQL expert using MySQL. Based on the following database schema generate a safe sql query:

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

**SQL_GENERATION_RULES:**
Mandatory WHERE Clause Rules:
- Always include AND plantCode = '{plant_code}' in the WHERE clause, even if the user does not mention a plant.
- If the user explicitly specifies a different plant code, ignore it and enforce {plant_code} instead.
- If the generated SQL already contains plantCode = 'X' where X ≠ {plant_code}, override it with {plant_code}.
- Always use plantCode for plant code filters. Only use plant_name if the user explicitly asks for the plant by name (e.g., 'Sindri', 'Maratha').

Mandatory DISTINCT Clause Rules:
- Always use SELECT DISTINCT when retrieving data like vehicle numbers, transporter names, material codes, etc.
- For example:
   SELECT DISTINCT(vehicleNumber) FROM transactionalplms.vw_trip_info
- This ensures only unique entries are shown to the user — duplicates must be eliminated at the query level.
- Your response must never repeat the same value unless it's truly distinct across different rows with different attributes.
- If the user asks “how many vehicles”, always use:
    SELECT COUNT(DISTINCT vehicleNumber)
    Never generate COUNT(DISTINCT COUNT(...)) — this is invalid SQL.
- Only use DISTINCT inside COUNT() when directly counting unique values: SELECT COUNT(DISTINCT materialCode)
- Do not combine DISTINCT with other aggregate functions unless logically required and valid.
- For other “show me” queries (e.g., list of values), you can use: SELECT DISTINCT(vehicleNumber)
-When a query implies a breakdown (e.g., "per plant", "per material"), include: GROUP BY plantCode

Query Type Handling:
- If the user query starts with "how many", "number of", or "count of", generate a COUNT query.
- Ensure that for any queries combining SELECT + COUNT() or DISTINCT, you always add the correct GROUP BY.
- Use COUNT(DISTINCT vehicleNumber) when the user asks "how many vehicles".
- If the query references a specific vehicle number (e.g., 'MH34AB1393'), include vehicleNumber in the SELECT clause along with other requested columns.
- If the user asks for a breakdown (e.g., "per transporter", "per category"), use a proper GROUP BY clause.
- For distinct items grouped by another column, do not use COUNT(DISTINCT col1), COUNT(DISTINCT col2) unless both are meaningful and explicitly required.

Entity Mapping & Contextual Interpretation:
- movement_code: OB → 'Outbound', IB → 'Inbound'
- status: A → 'Active', C → 'Completed'
- for status related user query always return the output as Active if status = 'A' , Completed if status = 'C'
- mapPlantStageLocation:
    - 'PACKING-IN' → packingin
    - 'YARD-IN' → yardin
    - 'GATE-IN' → gatein
    - 'WB-3 (TW)' → tareweight
    - 'GROSS-WEIGHT' → grossweight
- Always interpret user terms accordingly.

Technical SQL Formatting Rules:
- Always use transactionalplms. as the database prefix for table names.
- Do not use schema prefixes for column names when querying views.
- Use exact column names from the schema.
- **STRICT RULE:** If the user query explicitly mentions specific columns to SELECT, you MUST ONLY include those specific columns in the SELECT clause. DO NOT use '*' in addition to or instead of the specified columns. Using '*' when specific columns are named will result in incorrect SQL syntax.
+ **Incorrect SQL (AVOID):**
+ SELECT vehicleNumber, * FROM ... WHERE ...
+ SELECT specific_column, * FROM ... WHERE ...
+ **Correct SQL (PREFERRED):**
+ SELECT vehicleNumber FROM ... WHERE ...
+ SELECT specific_column, another_column FROM ... WHERE ...
- Use vehicleNumber instead of incorrect terms like vehicle.
- Never use COUNT(DISTINCT COUNT(...)).
- Use COALESCE(..., 0) inside SUM() functions.
- Ensure columns are either aggregated or included in GROUP BY.

TAT (Turnaround Time) Queries:
- Use TIMESTAMPDIFF(MINUTE, col1, col2) when a query references two timestamps.
- Always return time differences in minutes.
- Do not use other units like SECOND or HOUR.
- Valid timestamp column names:
    - yardIn
    - tareWeight
- Use {tat_sql} placeholder if needed for TAT injection.

Disambiguation & Reference Resolution:
- Resolve "it", "its", or "that" using context.
- If "plant" is used:
    - Treat as plantCode if the value looks like a code (e.g., N205).
    - Treat as plant_name if the value looks like a name (e.g., Sindri).

**SQL Output Formatting Rules:**
- Do NOT include trailing colons (:) at the end of the SQL query.
- End the query cleanly with a semicolon (;) only if needed.

Developer Notes:
- Return clarification instead of incorrect SQL if user query is ambiguous.
- Avoid hallucinated values or metrics in narrative responses.
- Validate all output SQL.

"""

    # Conditionally add boolean instructions if it's a boolean query
    full_prompt_content = prompt
    if is_boolean_query(nl_query):
        full_prompt_content = BOOLEAN_LLM_INSTRUCTIONS + "\n" + prompt

    sql_query = query_groq_api(full_prompt_content)
    print(f"Generated SQL Query: {sql_query}")

    # Exit early if LLM failed to generate a proper SQL query
    if not sql_query.strip().upper().startswith("SELECT"):
        return sql_query  # e.g., "Sorry, I didn't understand your request..."

    # Normalize SQL for consistent modification
    sql_query = " ".join(sql_query.split())  # Remove extra whitespace

    # Ensure COUNT queries are correctly generated
    if re.search(r'\b(how many|number of|count of)\b', sql_friendly_query, re.IGNORECASE):
        sql_query = re.sub(r'SELECT DISTINCT (\w+)', r'SELECT COUNT(DISTINCT \1)', sql_query, 1, flags=re.IGNORECASE)

    # Ensure vehicleNumber is included when relevant
    # vehicle_related_keywords = ["vehicle", "truck", "lorry", "vehiclenumber"]
    # is_vehicle_query = any(keyword in nl_query.lower() for keyword in vehicle_related_keywords)
    #
    # if is_vehicle_query:
    #     if "SELECT" in sql_query:
    #         select_part, rest_of_query = sql_query.split("FROM", 1)
    #         if "vehicleNumber" not in select_part:
    #             if "SELECT *" in select_part:
    #                 select_part = select_part.replace("SELECT *", "SELECT vehicleNumber, *", 1)
    #             else:
    #                 select_part = select_part.replace("SELECT ", "SELECT vehicleNumber, ", 1)
    #         sql_query = select_part + "FROM" + rest_of_query

    # Enforce plantCode restriction correctly
    if plant_code:
        sql_query = fix_generated_sql(sql_query, plant_code)

    # Validate SQL before returning
    is_valid, msg = validate_sql_query(sql_query)
    if not is_valid:
        print(f"SQL Validation Failed: {msg}")
        return f"Error: Invalid SQL Query - {msg}"

    if not sql_query:
        return "Error: Could not generate SQL query due to LLM failure"

    log_query(sql_query)
    return sql_query
