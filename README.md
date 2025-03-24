# Natural Language to SQL Chatbot (Context-Aware)

This is a context-aware chatbot capable of converting natural language queries into SQL queries to retrieve meaningful information from a relational database. It keeps track of conversation history and entities, making it capable of handling follow-ups and pronoun references like "it", "that", or "this vehicle".

🟢 Features:
- Converts natural language queries into MySQL queries dynamically using LLMs (Gemma-9B/Groq API).

- Maintains session-based entity tracking and conversation history for contextual understanding.

- Handles contextual references (e.g., pronouns like "that", "it").

- Provides clean, human-readable, Markdown-formatted responses.

- Supports predefined responses for general queries like greetings or help.

- Logs full conversation history for analysis.

- Includes feedback mechanism and session clearing endpoint.

