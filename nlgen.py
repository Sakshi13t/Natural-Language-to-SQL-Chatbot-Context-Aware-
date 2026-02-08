import os
from dotenv import load_dotenv
import json
import logging
import requests
from decimal import Decimal
from datetime import datetime

# Load environment variables
load_dotenv()

# Setup Logging
logging.basicConfig(
    filename="query_logs.txt",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# Load credentials
NLGEN_GROQ_API_KEY = os.getenv("NLGEN_GROQ_API_KEY")  # Groq API Key
LLM_API_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"  # Groq's OpenAI compatible endpoint

STATUS_MAPPING = {  # Define status mapping
    "A": "Active",
    "C": "Completed"
}

# Load Predefined Responses
with open("predefined_responses.json", "r") as f:
    predefined_responses = json.load(f)


def format_bot_response(column_name, value, structured=False):
    """
    Formats a single data point for natural language output.

    Args:
        column_name (str): The column name from the database result.
        value (any): The value from the database result.
        structured (bool, optional): If True, returns a string suitable for structured output.
            If False, returns a more natural language-style string. Defaults to False.

    Returns:
        str: The formatted string.
    """
    # Convert Decimal to float if necessary
    if isinstance(value, Decimal):
        value = float(value)

    # Improved: Dynamically create pretty column name
    pretty_col = column_name.replace("_", " ").title()  # Basic transformation

    if "tat" in column_name.lower() and value < 0:
        value = abs(value)  # Convert negative TAT to positive for logical consistency
        if structured:
            return f"Turnaround Time: {value} minutes (Note: There was an anomaly in the data indicating a negative value.)"
        return f"The turnaround time is {value} minutes. (Note: The original value was negative, which might indicate an issue in the data.)"

    if value is None:
        if structured:
            return f"{pretty_col}: Unavailable"
        return f"The {pretty_col.lower()} is unavailable."

    if "tat" in column_name.lower():
        pretty_col = "Turnaround Time"  # Handle TAT more clearly

    if "status" in column_name.lower():
        status_readable = STATUS_MAPPING.get(str(value).upper(), value)
        if structured:
            return f"{pretty_col}: {status_readable}"
        return f"The {pretty_col.lower()} is {status_readable}."

    if "date" in column_name.lower():
        try:
            date_obj = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")  # Adjust format if needed
            formatted_date = date_obj.strftime("%B %d, %Y, %I:%M %p")
            if structured:
                return f"{pretty_col}: {formatted_date}"
            return f"The {pretty_col.lower()} is {formatted_date}."
        except ValueError:
            if structured:
                return f"{pretty_col}: {value}"
            return f"The {pretty_col.lower()} is {value}."  # Return original if parsing fails

    if "weight" in column_name.lower():
        if structured:
            return f"{pretty_col}: {value} kg"  # Add unit
        return f"The {pretty_col.lower()} is {value} kg."

    if "capacity" in column_name.lower():
        if structured:
            return f"{pretty_col}: {value}"
        return f"The {pretty_col.lower()} is {value}."

    if "number" in column_name.lower() or "code" in column_name.lower():
        if structured:
            return f"{pretty_col}: {value}"
        return f"The {pretty_col.lower()} is {value}."

    if structured:
        return f"{pretty_col}: {value}"
    return f"The {pretty_col.lower()} is {value}"


def detect_primary_entity(column_names, sql_result, user_query):
    """
    Detects the primary entity (e.g., vehicle, trip) in the query for better response formatting.

    Args:
        column_names (list): List of column names.
        sql_result (dict): The full SQL result dictionary.
        user_query (str): The original user query.

    Returns:
        str: The name of the primary entity column, or None if not found.
    """
    # Prioritize certain entities
    if "vehicleNumber" in column_names:
        return "vehicleNumber"
    if "tripId" in column_names:
        return "tripId"
    if "plant_name" in column_names:
        return "plant_name"

    # Basic keyword detection in the query
    if "vehicle" in user_query.lower() or "truck" in user_query.lower() or "lorry" in user_query.lower():
        if "vehicleNumber" in column_names:
            return "vehicleNumber"
    if "trip" in user_query.lower():
        if "tripId" in column_names:
            return "tripId"
    if "plant" in user_query.lower():
        if "plant_name" in column_names:
            return "plant_name"

    return None  # Default


def convert_decimal_to_float(obj):
    """Convert Decimal values to float recursively."""
    if isinstance(obj, dict):
        return {key: convert_decimal_to_float(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimal_to_float(item) for item in obj]
    elif isinstance(obj, Decimal):
        return float(obj)
    return obj


def generate_natural_language_response(sql_result, user_query):
    """
    Generates a natural language response from the structured SQL output using Llama 3 via Groq.

    Args:
        sql_result (dict): The dictionary returned by execute_sql (dictionary with "columns" and "data").
        user_query (str): The original user query.

    Returns:
        str: The natural language response.
    """

    if "error" in sql_result:
        return f"Sorry, there was an error: {sql_result['error']}"

    columns = sql_result.get("columns", [])
    data = sql_result.get("data", [])

    if not columns or not data:
        return "I found no matching results in the database."

    # 1. Prepare Data Representation (for Llama 3 prompt)
    data_string = ""
    for row in data:
        row_dict = dict(zip(columns, row))
        # Convert Decimal objects to float and datetime objects to string
        for key, val in row_dict.items():
            if isinstance(val, Decimal):
                row_dict[key] = float(val)  # Convert Decimal to float
            elif isinstance(val, datetime):
                row_dict[key] = val.strftime("%Y-%m-%d %H:%M:%S")  # Convert datetime to string
        data_string += json.dumps(row_dict) + "\n"

    # 2. Construct Prompt (for Llama 3)
    prompt = f"""
    You are a helpful assistant that translates database query results into human-readable text.
    Here is the original user query:
    "{user_query}"

    Here is the data from the database:
    ```json
    {data_string}
    ```
    Use the data to answer the user's query clearly and concisely. Respond in markdown format with the following guidelines:
    **MANDATORY: Opening Sentence Rules**
    - Your response MUST begin with a **natural, friendly sentence** that directly reflects the user’s query.
    - DO NOT use or prepend **any** of the following:
      - “Here is the response…”
      - “Here is the response in markdown format”
      - “The user asked…”
      - “Answer:”
      - “Response:”
      - “Here’s what I found in markdown:”
    - Do NOT add any **formatting or meta-commentary** about the response itself (e.g., markdown, format, structure). Just respond like a friendly human would.
    - Start with **exactly one** of the following opening lines and do not repeat:
      - “Based on your request, here’s what I found:”
      - “Sure! Here’s the info you requested:”
      
    **Tone & Structure:**
    Start with a friendly, natural-sounding sentence that reflects the user's intent. 
    
    Provide a short summary or insight first, followed by details.
    - Use bullet points for simple lists (e.g., material codes, vehicle numbers).
    - Use a clean layout — avoid headings (##) or triple backticks (```).
    
    **Specific Instruction for Potential Boolean Context:**
    - If the original user query was a question that likely expects a "yes" or "no" answer (e.g., starts with "is", "are", "does", "can", "whether", "if", "has", "have"), and the database result contains data, provide a concise answer that confirms or denies the condition implied by the query. Avoid adding extra notes about what the query *didn't* ask for. Focus on directly addressing the implied boolean question based on the data.
    
    **Data Interpretation Rules:**
    - If Turnaround Time (TAT) values are negative or very high (e.g., >10,000 minutes), flag them as potential anomalies with a note like:
      “This value may indicate a data issue.”
    - For long durations, show converted units as well:
      9,583 minutes (~6.7 days)
    - If any important fields (e.g., driver name, timestamps) are missing or null, explicitly state:
      “This information is not available.”

    **Terminology Notes:**
    - “TAT” means Turnaround Time: the time difference in minutes between two stage timestamps.
    - is user query asks for status , always return the mapped status :
       'A' : 'Active'
       'C' : 'Completed'
    - “dinumber” or “di” both refer to Delivery Invoice — treat them as the same.
    - "IGP","igp" or "igpnumber all refers to IGP (inward gate pass) - treat them as the same.
    -  If the query is vague but includes “TAT,” assume the user wants the duration between stages.

    **Response Guidelines:**
    - Clear: Use simple, conversational language.
    - Concise: Don’t repeat or over-explain.
    - Relevant: Only include fields or metrics that relate to the user’s query.
    - Structured: Organize content with bullets or short, easy-to-scan paragraphs.
    - When listing vehicle details (or similar records), show **each row** (vehicle) **as a separate bullet** or short paragraph.
        Example format:
        • Vehicle Number: X, Material Code: Y, Capacity: Z, Transporter: T
    
    ** Absolutely do NOT mention or suggest any value (e.g., material codes, transporter names, vehicle numbers) that is not explicitly present in the provided data.**
    - Only use exact values that exist in the JSON. Do not create, assume, or infer possible values.
    - Do not list any value if its count is zero or it doesn't exist in the data.
    - If the user asks about something (e.g., “COMPAM”) and it's **not in the data**, respond: “COMPAM is not present in the data.”

    **Row Formatting (MANDATORY):**
    - You must include all rows from the data — do not skip, summarize, or limit them unless the user says "top N".
    - Each dictionary in the data represents one record (e.g., one vehicle).
    - Present each record as **one bullet point**, containing all important fields.
    - DO NOT list fields one by one across bullets (e.g., vehicle number on 5 bullets).
    - DO NOT repeat the same field (like Vehicle Number or Material Code) unless it occurs in a **different record**.

    - If several rows have the **same value** (e.g., same transporter or material code), **only show it once per row** — do not list the same value five times.
    - Only list the top N rows that match the query — don’t aggregate or summarize unless asked.
    - Avoid using technical language, raw JSON, or internal data structures. The output should feel like it was written for a business user with no technical background.
    - Don’t summarize fields or stages not mentioned in the query.
    - Only refer to fields and values explicitly present in the data. Do not make assumptions or generate information that isn’t shown.

    **Validation Checks (if applicable):**
    - Transporter count = number of unique transporter names.
    - Vehicle count = number of unique vehicle numbers. Double-check for duplicates.

    **Wrap-Up:**
    - End with a polite line like:
        “Hope this helps!”
        “Let me know if you need anything else.”
        “Feel free to ask if you’d like more details!”
        
    **Final Validation (Strict):**
    - Your response must contain only **one** opening sentence from the approved list.
    - Do NOT include any preamble, markdown comment, or explanation of the response format.
    
    **Important:**
    - Do not include any explanation of how or why the response is formatted.
    - Do not mention following instructions, markdown, JSON, guidelines, or the user query.
    - Do not write notes, clarifications, or editorial comments.
    - Output only the user-facing content — nothing else.
    """

    # 3. Call Llama 3 API (via Groq)
    headers = {
        "Authorization": f"Bearer {NLGEN_GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "llama3-8b-8192",
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant that answers clearly and concisely based on database query results. Do not include meta-commentary or markdown formatting explanations."
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
        # "messages": [{"role": "user", "content": prompt}]
    }

    try:
        response = requests.post(LLM_API_ENDPOINT, headers=headers, json=payload,
                                 timeout=60)  # Adjust timeout as needed
        response.raise_for_status()
        llm_response = response.json()['choices'][0]['message']['content'].strip()

        if not llm_response or not llm_response.strip() or llm_response.lower() in {"n/a", "null", "none",
                                                                                    "i don't know", "no data",
                                                                                    "no response"}:
            logging.warning(
                f"LLM returned an unhelpful or blank response: '{llm_response!r}'")  # Use !r for raw representation
            fallback_message = "Sorry, I couldn't generate a helpful response for that query. Please try rephrasing or asking something different."
            logging.info(f"Returning fallback response: '{fallback_message}'")
            return fallback_message
        logging.info(f"[Groq/Llama 3 NLG Response]: {llm_response}")
        return llm_response  # Return response generated by LLM with the footer
    except requests.exceptions.RequestException as e:
        error_message = f"Groq/Llama 3 API error: {e}"
        print(error_message)
        logging.error(error_message)
        return f"Error: I encountered an error communicating with the language model: {e}"
    except json.JSONDecodeError as e:
        error_message = f"JSON Decode Error: {e}.  Response Text: {response.text}"
        print(error_message)
        logging.error(error_message)
        return "Error: Invalid JSON response from Groq API."
    except Exception as e:
        error_message = f"Unexpected error in generate_natural_language_response: {e}"
        print(error_message)
        logging.error(error_message)
        return f"Error: An unexpected error occurred: {e}"

