import streamlit as st
import pandas as pd
import sqlite3
import re
import io

# Attempt to import genai and handle if not found
try:
    from google import genai
except ImportError:
    st.error("The 'google-generativeai' library is not installed. Please install it: pip install google-generativeai")
    st.stop()

# --- Configuration & Helper Functions ---
DB_NAME = "streamlit_data.db" # Use a file-based DB for simplicity across calls, or manage connection object
TABLE_NAME = "transactions"

def load_df_to_sqlite(df, db_path=DB_NAME, table_name=TABLE_NAME):
    """Loads a DataFrame into an SQLite table."""
    conn = sqlite3.connect(db_path)
    try:
        df.to_sql(table_name, conn, if_exists="replace", index=False)
        st.session_state.db_path_for_query = db_path # Store for querying
        st.session_state.table_loaded = True
    except Exception as e:
        st.error(f"Error loading DataFrame to SQLite: {e}")
        st.session_state.table_loaded = False
    finally:
        conn.close()

# Function to generate SQL query using Google Gemini API
def generate_sql_query(client_instance, model_id_str, user_prompt_str, headers_list):
    if not client_instance:
        st.error("Gemini client not initialized. Please provide an API key.")
        return None
    context = (
        f"You are an expert SQL generator. The SQLite database table is named '{TABLE_NAME}'. " # Use constant
        f"The table has the following columns (headers): {', '.join(headers_list)}. "
        f"Based on the user's request: '{user_prompt_str}', generate ONLY the SQL query that can be directly executed. "
        f"Do not include any explanations, apologies, or introductory/concluding text. "
        f"Ensure the query is valid for SQLite. The table name MUST BE '{TABLE_NAME}'." # Use constant
    )
    try:
        with st.spinner("🤖 Generating SQL query with Gemini..."):
            response = client_instance.models.generate_content(
                model=model_id_str,
                contents=context
            )
        raw_response = response.text.strip()
        sql_query_match = re.search(r"```(?:sql)?\s*(.*?)\s*```", raw_response, re.DOTALL | re.IGNORECASE)
        if sql_query_match:
            sql_query = sql_query_match.group(1).strip()
        else:
            sql_query = re.sub(r"^(sql\s*)?(SELECT\s|INSERT\s|UPDATE\s|DELETE\s|CREATE\s|ALTER\s|DROP\s)",
                               lambda m: m.group(2) or m.group(3) or m.group(4) or m.group(5) or m.group(6) or m.group(7) or m.group(8),
                               raw_response, flags=re.IGNORECASE).strip()
            if not sql_query.upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE", "WITH")):
                 st.warning("LLM did not return a query in expected format. Using raw response. Please verify.")
                 sql_query = raw_response
        return sql_query
    except Exception as e:
        st.error(f"Error calling Gemini API: {e}")
        return None

# Function to execute SQL query on SQLite database
def execute_query_on_db(query_str, db_path=DB_NAME): # Expects db_path
    """Executes an SQL query on the specified SQLite database file."""
    if not os.path.exists(db_path) and db_path != ":memory:": # Check if db file exists
        return "Database file not found. Please load CSV first."
    if not st.session_state.get("table_loaded", False) and db_path == ":memory:":
         return "Table not loaded into in-memory database for this session. Please re-load CSV."


    conn = sqlite3.connect(db_path)
    try:
        result_df = pd.read_sql_query(query_str, conn)
        return result_df
    except Exception as e:
        # Provide more specific error if table not found
        if "no such table" in str(e).lower() and TABLE_NAME in str(e).lower():
            return (f"Error executing query: {str(e)}. "
                    f"The table '{TABLE_NAME}' might not have been loaded correctly or the database connection was reset. "
                    "Try re-loading the CSV.")
        return f"Error executing query: {str(e)}"
    finally:
        conn.close()

# Streamlit App
def main():
    st.set_page_config(page_title="CSV-to-SQL Query Builder", layout="wide")
    st.title("📄 CSV to SQLite Query Builder with Gemini AI 🚀")

    # Initialize session state variables
    if "gemini_client" not in st.session_state:
        st.session_state.gemini_client = None
    if "headers" not in st.session_state:
        st.session_state.headers = None
    if "uploaded_file_name" not in st.session_state:
        st.session_state.uploaded_file_name = None
    if "df_loaded" not in st.session_state: # To store the DataFrame itself
        st.session_state.df_loaded = None
    if "table_loaded" not in st.session_state:
        st.session_state.table_loaded = False
    if "db_path_for_query" not in st.session_state:
        st.session_state.db_path_for_query = DB_NAME # Default to file-based

    # --- Sidebar for Configuration ---
    with st.sidebar:
        st.header("⚙️ Configuration")
        api_key_input = st.text_input("Enter your Google Gemini API Key:", type="password", key="api_key_val")
        model_id_input = st.text_input("Gemini Model ID:", value="gemini-1.5-flash-latest", key="model_id_val")

        use_in_memory_db = st.checkbox("Use In-Memory Database (faster, non-persistent)", value=False, key="use_memory_db")
        if use_in_memory_db:
            st.session_state.db_path_for_query = ":memory:"
            st.caption("In-memory DB is reset if app reloads or CSV changes.")
        else:
            st.session_state.db_path_for_query = DB_NAME
            st.caption(f"Using file: {DB_NAME}")


        if st.button("Initialize Gemini Client", key="init_client_btn"):
            if api_key_input:
                try:
                    st.session_state.gemini_client = genai.Client(api_key=api_key_input)
                    st.success("Gemini client initialized successfully!")
                except Exception as e:
                    st.error(f"Failed to initialize Gemini client: {e}")
                    st.session_state.gemini_client = None
            else:
                st.warning("Please enter an API key.")
        if st.session_state.gemini_client: st.success("Gemini Client is active.")

    # --- Main Area ---
    st.header("1. Upload CSV File")
    uploaded_file = st.file_uploader("Choose a CSV file", type=["csv"], key="csv_uploader")

    if uploaded_file is not None:
        # If a new file is uploaded or the file name changes, reset df and table status
        if st.session_state.uploaded_file_name != uploaded_file.name:
            st.session_state.df_loaded = None
            st.session_state.table_loaded = False
            st.session_state.headers = None
            st.session_state.uploaded_file_name = uploaded_file.name
            if st.session_state.db_path_for_query != ":memory:" and os.path.exists(st.session_state.db_path_for_query):
                try:
                    os.remove(st.session_state.db_path_for_query) # Clean up old db file if not in memory
                    st.info(f"Removed old database file: {st.session_state.db_path_for_query}")
                except Exception as e_del:
                    st.warning(f"Could not remove old db file {st.session_state.db_path_for_query}: {e_del}")


        if st.session_state.df_loaded is None: # Load DF if not already loaded for this file
            try:
                csv_content = uploaded_file.getvalue().decode('utf-8')
                st.session_state.df_loaded = pd.read_csv(io.StringIO(csv_content))
                st.session_state.headers = st.session_state.df_loaded.columns.tolist()
                st.success(f"CSV '{uploaded_file.name}' parsed successfully.")
            except Exception as e:
                st.error(f"Error parsing CSV file: {e}")
                st.session_state.df_loaded = None
                st.session_state.headers = None

        if st.session_state.df_loaded is not None:
            if not st.session_state.table_loaded: # Load to SQL if not already done for this DF
                with st.spinner(f"Loading data into SQLite table '{TABLE_NAME}'..."):
                    load_df_to_sqlite(st.session_state.df_loaded, db_path=st.session_state.db_path_for_query, table_name=TABLE_NAME)
                if st.session_state.table_loaded:
                    st.success(f"Data loaded into table '{TABLE_NAME}' in database '{st.session_state.db_path_for_query}'.")
                else:
                    st.error("Failed to load data into SQLite table.")

    if st.session_state.table_loaded and st.session_state.headers:
        st.write(f"### Available CSV Headers (Table: '{TABLE_NAME}'):")
        st.info(f"`{', '.join(st.session_state.headers)}`")

        st.header("2. Generate SQL Query")
        prompt = st.text_area("Enter your query in natural language:",
                              placeholder="e.g., 'Show me all records where product is Laptop and sales are greater than 500'",
                              key="nl_prompt", height=100)

        if st.button("✨ Generate SQL Query", key="generate_sql_btn", disabled=not st.session_state.gemini_client):
            if not st.session_state.gemini_client:
                st.error("Gemini client not initialized. Please provide API key and initialize in the sidebar.")
            elif not prompt:
                st.warning("Please enter a natural language query.")
            else:
                sql_query = generate_sql_query(
                    st.session_state.gemini_client,
                    model_id_input,
                    prompt,
                    st.session_state.headers
                )
                st.session_state.generated_sql_query = sql_query
                if sql_query:
                    st.write("### 🤖 Generated SQL Query:")
                    st.code(sql_query, language="sql")
                else:
                    st.error("Could not generate SQL query.")

        if st.session_state.get("generated_sql_query"):
            st.header("3. Execute Query & View Results")
            if st.button("⚡ Execute Generated SQL Query", key="execute_sql_btn"):
                if not st.session_state.table_loaded:
                    st.error("Table not loaded. Please ensure CSV is uploaded and processed.")
                else:
                    # Crucial: If in-memory, ensure the table is present in *this session's view* of the in-memory DB
                    # For file-based, this is less of an issue if the file persists.
                    # For robustness with :memory:, one might reload the df into a new connection here.
                    # However, our `load_df_to_sqlite` should make it available via `st.session_state.db_path_for_query`
                    # The key is that `execute_query_on_db` now uses this path.

                    # If using in-memory, and to be absolutely sure, we might re-populate the table
                    # just before querying if we suspect connection issues.
                    # However, the current design with `st.session_state.db_path_for_query`
                    # and loading `st.session_state.df_loaded` into that path should work.
                    if st.session_state.db_path_for_query == ":memory:" and not st.session_state.table_loaded:
                         with st.spinner("Re-populating in-memory table before query..."):
                              load_df_to_sqlite(st.session_state.df_loaded, db_path=":memory:", table_name=TABLE_NAME)


                    with st.spinner("Executing query..."):
                        results = execute_query_on_db(
                            st.session_state.generated_sql_query,
                            db_path=st.session_state.db_path_for_query # Use the stored path
                        )
                    if isinstance(results, pd.DataFrame):
                        st.write("### 📊 Query Results:")
                        st.dataframe(results, use_container_width=True)
                        csv_export = results.to_csv(index=False).encode('utf-8')
                        st.download_button(
                            label="📥 Download Results as CSV",
                            data=csv_export,
                            file_name="query_results.csv",
                            mime="text/csv",
                        )
                    else:
                        st.error(f"Query Execution Failed: {results}")
    elif uploaded_file:
        st.info("Processing uploaded CSV. Please wait or check for errors above.")
    else:
        st.info("Please upload a CSV file to begin.")

    # Cleanup for file-based DB on exit (optional, good practice)
    # This is tricky in Streamlit as there's no explicit "exit" event for the script itself.
    # The file will persist unless manually deleted or handled at the start of a new session.
    # The current code removes the old DB file if a *new* file is uploaded and not using :memory:.

if __name__ == "__main__":
    import os # for os.path.exists and os.remove
    main()
