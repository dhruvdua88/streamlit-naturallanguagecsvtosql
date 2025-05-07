import streamlit as st
import pandas as pd
import sqlite3
import re
import io
import os
import json

# Attempt to import genai and handle if not found
try:
    from google import genai
except ImportError:
    st.error("The 'google.genai' module is not found. Please ensure 'google-generativeai' or the relevant Google AI library is installed.")
    st.stop()

# --- Configuration & Helper Functions ---
DB_NAME = "streamlit_data.db"
TABLE_NAME = "transactions" # User-specified table name - THIS WILL BE USED

def dataframe_to_optimized_json(df):
    try:
        json_string = df.to_json(orient="records", lines=False, date_format="iso", default_handler=str)
        return json_string
    except Exception as e:
        st.error(f"Error converting DataFrame to JSON: {e}")
        return None

def load_df_to_sqlite(df, db_path=DB_NAME, table_name=TABLE_NAME): # table_name param will receive the desired name
    conn = sqlite3.connect(db_path)
    try:
        # The table_name argument is expected to be the already decided name (e.g., "transactions")
        # Basic sanitization is good practice if table_name could come from less controlled sources,
        # but "transactions" is safe.
        sane_table_name = "".join(c if c.isalnum() else "_" for c in table_name)
        if not sane_table_name: # Fallback if sanitization results in empty string
             sane_table_name = "default_imported_data" # More descriptive fallback

        df.to_sql(sane_table_name, conn, if_exists="replace", index=False)
        st.session_state.db_path_for_query = db_path
        st.session_state.table_loaded = True
        st.session_state.current_table_name = sane_table_name # Store the name actually used
    except Exception as e:
        st.error(f"Error loading DataFrame to SQLite: {e}")
        st.session_state.table_loaded = False
    finally:
        conn.close()

def generate_sql_query(client_instance: genai.Client, model_id_str: str, user_prompt_str: str, headers_list: list, current_table_name_for_llm: str):
    if not client_instance:
        st.error("Gemini client not initialized.")
        return None
    if not model_id_str:
        st.error("Gemini Model ID not provided.")
        return None
    
    context = (
        f"You are an expert SQL generator. The SQLite database table is named '{current_table_name_for_llm}'. "
        f"The table has the following columns (headers): {', '.join(headers_list)}. "
        f"Based on the user's request: '{user_prompt_str}', generate ONLY the SQL query. "
        f"Do not include explanations. Ensure the query is valid for SQLite. The table name MUST BE '{current_table_name_for_llm}'."
    )
    try:
        with st.spinner("🤖 Generating SQL query..."):
            response = client_instance.models.generate_content(
                model=model_id_str,
                contents=context
            )
        raw_response = response.text.strip()
        sql_query_match = re.search(r"```(?:sql)?\s*(.*?)\s*```", raw_response, re.DOTALL | re.IGNORECASE)
        if sql_query_match:
            sql_query = sql_query_match.group(1).strip()
        else:
            sql_query = re.sub(r"^\s*sql\s+", "", raw_response, flags=re.IGNORECASE).strip()
            if not sql_query.upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE", "WITH", "CREATE", "ALTER", "DROP")):
                 st.warning(f"LLM did not return query in expected format. Using raw: '{sql_query}'")
        return sql_query
    except Exception as e:
        st.error(f"Error calling Gemini API: {e}")
        return None

def execute_query_on_db(query_str, db_path=DB_NAME, current_table_name_for_error_msg=TABLE_NAME):
    if not os.path.exists(db_path) and db_path != ":memory:":
        return "Database file not found."
    if not st.session_state.get("table_loaded", False) and db_path == ":memory:":
         return "Table not loaded into in-memory DB."

    conn = sqlite3.connect(db_path)
    try:
        result_df = pd.read_sql_query(query_str, conn)
        return result_df
    except Exception as e:
        if "no such table" in str(e).lower() and current_table_name_for_error_msg in str(e).lower():
            return (f"Error: {str(e)}. Table '{current_table_name_for_error_msg}' not found. Reload file.")
        return f"Error executing query: {str(e)}"
    finally:
        conn.close()

def main():
    st.set_page_config(page_title="File-to-SQL Query Builder", layout="wide")
    st.title("📄 File (CSV/XLSX) to SQLite Query Builder with Gemini AI 🚀")

    # Initialize session state variables
    if "gemini_client" not in st.session_state: st.session_state.gemini_client = None
    if "gemini_model_id" not in st.session_state: st.session_state.gemini_model_id = "gemini-1.5-flash-latest"
    if "headers" not in st.session_state: st.session_state.headers = None
    if "uploaded_file_name" not in st.session_state: st.session_state.uploaded_file_name = None
    if "df_loaded" not in st.session_state: st.session_state.df_loaded = None
    if "table_loaded" not in st.session_state: st.session_state.table_loaded = False
    if "db_path_for_query" not in st.session_state: st.session_state.db_path_for_query = DB_NAME
    if "json_data_for_download" not in st.session_state: st.session_state.json_data_for_download = None
    if "generated_sql_query" not in st.session_state: st.session_state.generated_sql_query = None
    # --- FIX for Table Name ---
    # Ensure current_table_name defaults to and is reset to the global TABLE_NAME
    if "current_table_name" not in st.session_state:
        st.session_state.current_table_name = TABLE_NAME


    with st.sidebar:
        st.header("⚙️ Configuration")
        api_key_input = st.text_input("Enter your Google Gemini API Key:", type="password", key="api_key_val_sidebar")
        model_id_input_sidebar = st.text_input("Gemini Model ID:", 
                                               value=st.session_state.gemini_model_id, 
                                               key="model_id_val_sidebar")

        use_in_memory_db = st.checkbox("Use In-Memory Database", value=False, key="use_memory_db_sidebar")
        if use_in_memory_db:
            st.session_state.db_path_for_query = ":memory:"
            st.caption("In-memory DB is reset if app reloads or file changes.")
        else:
            st.session_state.db_path_for_query = DB_NAME
            st.caption(f"Using file: {DB_NAME}")

        if st.button("Initialize Gemini Client", key="init_client_btn_sidebar"):
            if api_key_input and model_id_input_sidebar:
                try:
                    st.session_state.gemini_client = genai.Client(api_key=api_key_input)
                    st.session_state.gemini_model_id = model_id_input_sidebar
                    test_response = st.session_state.gemini_client.models.generate_content(
                        model=st.session_state.gemini_model_id, contents="test"
                    )
                    if not test_response.text: raise Exception("Test call to Gemini returned empty response.")
                    st.success("Gemini client initialized!")
                except Exception as e:
                    st.error(f"Failed to initialize Gemini client: {e}")
                    st.session_state.gemini_client = None
            else:
                st.warning("Please enter API key and Model ID.")
        if st.session_state.gemini_client: st.success("Gemini Client is active.")

    st.header("1. Upload CSV or XLSX File")
    uploaded_file = st.file_uploader("Choose a CSV or XLSX file", type=["csv", "xlsx"], key="file_uploader_main_corrected")
    
    if uploaded_file is not None:
        # --- FIX for Table Name ---
        # When a new file is uploaded, ensure current_table_name is reset to the global TABLE_NAME
        if st.session_state.uploaded_file_name != uploaded_file.name:
            st.session_state.df_loaded = None
            st.session_state.table_loaded = False
            st.session_state.headers = None
            st.session_state.json_data_for_download = None
            st.session_state.generated_sql_query = None 
            st.session_state.uploaded_file_name = uploaded_file.name
            st.session_state.current_table_name = TABLE_NAME # Ensure it's "transactions"
            
            if st.session_state.db_path_for_query != ":memory:" and os.path.exists(st.session_state.db_path_for_query):
                try:
                    os.remove(st.session_state.db_path_for_query)
                    st.info(f"Removed old database file: {st.session_state.db_path_for_query}")
                except Exception as e_del:
                    st.warning(f"Could not remove old db file {st.session_state.db_path_for_query}: {e_del}")

        if st.session_state.df_loaded is None:
            try:
                file_name_for_parse = uploaded_file.name
                df_temp = None
                if file_name_for_parse.endswith('.csv'):
                    df_temp = pd.read_csv(uploaded_file)
                elif file_name_for_parse.endswith(('.xls', '.xlsx')):
                    df_temp = pd.read_excel(uploaded_file)
                
                if df_temp is not None:
                    current_df = df_temp.copy()
                    
                    # --- FIX for Header Cleaning & Debugging ---
                    original_headers = current_df.columns.tolist()
                    # st.write("Debug: Original Headers from file:", original_headers) # For UI debug
                    print("Debug: Original Headers from file:", original_headers)


                    # Ensure all column names are strings before processing
                    current_df.columns = [str(col) for col in original_headers]
                    stringified_headers = current_df.columns.tolist()
                    # st.write("Debug: Headers after string conversion:", stringified_headers) # For UI debug
                    print("Debug: Headers after string conversion:", stringified_headers)


                    if any('$' in str(col_name) for col_name in stringified_headers): # Check on stringified headers
                        st.info("'$' found in a column header. Removing '%' from all column headers.")
                        
                        headers_before_percent_removal = current_df.columns.tolist()
                        # st.write("Debug: Headers before '%' removal:", headers_before_percent_removal) # For UI debug
                        print("Debug: Headers before '$' removal:", headers_before_percent_removal)

                        modified_headers = [col_name.replace('$', '') for col_name in headers_before_percent_removal]
                        current_df.columns = modified_headers
                        
                        # st.write("Debug: Headers after '%' removal attempt:", current_df.columns.tolist()) # For UI debug
                        print("Debug: Headers after '%' removal attempt:", current_df.columns.tolist())
                    
                    st.session_state.df_loaded = current_df # df_loaded gets the potentially modified df
                    st.session_state.headers = st.session_state.df_loaded.columns.tolist() # Update headers from the processed df
                    
                    st.success(f"File '{file_name_for_parse}' parsed. Processed Headers: {st.session_state.headers}")
                    # st.write("Debug: Final st.session_state.headers:", st.session_state.headers) # For UI debug
                    print("Debug: Final st.session_state.headers:", st.session_state.headers)


                    json_str_output = dataframe_to_optimized_json(st.session_state.df_loaded)
                    if json_str_output:
                        st.session_state.json_data_for_download = json_str_output.encode('utf-8')
                        st.info("DataFrame successfully converted to optimized JSON.")
            except Exception as e:
                st.error(f"Error parsing file or processing headers: {e}")
                st.session_state.df_loaded = None
                st.session_state.headers = None
                st.session_state.json_data_for_download = None

        if st.session_state.df_loaded is not None:
            if st.session_state.json_data_for_download:
                download_file_name_base = os.path.splitext(st.session_state.uploaded_file_name)[0]
                st.download_button(
                    label="📥 Download Data as Optimized JSON",
                    data=st.session_state.json_data_for_download,
                    file_name=f"{download_file_name_base}_converted.json",
                    mime="application/json",
                    key="download_json_btn_corrected"
                )

            if not st.session_state.table_loaded:
                # --- FIX for Table Name ---
                # Pass the st.session_state.current_table_name (which should be "transactions")
                with st.spinner(f"Loading data into SQLite table '{st.session_state.current_table_name}'..."):
                    load_df_to_sqlite(st.session_state.df_loaded,
                                      db_path=st.session_state.db_path_for_query,
                                      table_name=st.session_state.current_table_name) 
                if st.session_state.table_loaded:
                    st.success(f"Data loaded into table '{st.session_state.current_table_name}' in database '{st.session_state.db_path_for_query}'.")
                else:
                    st.error("Failed to load data into SQLite table.")

    if st.session_state.table_loaded and st.session_state.headers:
        # --- FIX for Table Name ---
        # Display uses st.session_state.current_table_name
        st.write(f"### Available Table Headers (Table: '{st.session_state.current_table_name}'):")
        st.info(f"`{', '.join(st.session_state.headers)}`")

        tab1, tab2 = st.tabs(["🤖 AI SQL Generation", "✏️ Direct SQL Execution"])

        with tab1:
            st.header("2. Generate SQL Query with AI")
            # --- FIX for Table Name ---
            # Prompt placeholder also uses st.session_state.current_table_name
            prompt = st.text_area("Enter your query in natural language:",
                                  placeholder=f"e.g., 'Show me all records from {st.session_state.current_table_name} where product is Laptop'",
                                  key="nl_prompt_corrected", height=100)

            if st.button("✨ Generate SQL Query", key="generate_sql_btn_corrected", disabled=not st.session_state.gemini_client):
                if not st.session_state.gemini_client:
                    st.error("Gemini client not initialized.")
                elif not prompt:
                    st.warning("Please enter a natural language query.")
                else:
                    sql_query = generate_sql_query(
                        st.session_state.gemini_client,
                        st.session_state.gemini_model_id,
                        prompt,
                        st.session_state.headers,
                        st.session_state.current_table_name # Pass the correct table name
                    )
                    st.session_state.generated_sql_query = sql_query
                    if sql_query:
                        st.write("### 🤖 Generated SQL Query:")
                        st.code(sql_query, language="sql")
                    else:
                        st.error("Could not generate SQL query.")

            if st.session_state.get("generated_sql_query"):
                st.markdown("---") 
                if st.button("⚡ Execute Generated SQL Query", key="execute_generated_sql_btn_corrected"):
                    with st.spinner("Executing generated query..."):
                        results = execute_query_on_db(
                            st.session_state.generated_sql_query,
                            db_path=st.session_state.db_path_for_query,
                            current_table_name_for_error_msg=st.session_state.current_table_name
                        )
                    if isinstance(results, pd.DataFrame):
                        st.write("### 📊 AI Query Results:")
                        st.dataframe(results, use_container_width=True)
                        csv_export = results.to_csv(index=False).encode('utf-8')
                        st.download_button(
                            label="📥 Download AI Query Results as CSV",
                            data=csv_export, file_name="ai_query_results.csv",
                            mime="text/csv", key="download_ai_results_btn_corrected"
                        )
                    else:
                        st.error(f"AI Query Execution Failed: {results}")

        with tab2:
            st.header("3. Execute Direct SQL Query")
             # --- FIX for Table Name ---
            direct_sql_query = st.text_area("Enter your SQL query directly:",
                                            placeholder=f"e.g., SELECT * FROM {st.session_state.current_table_name} WHERE YourColumn = 'YourValue';",
                                            key="direct_sql_input_corrected", height=150)
            if st.button("⚡ Execute Direct SQL Query", key="execute_direct_sql_btn_corrected"):
                if not direct_sql_query.strip():
                    st.warning("Please enter an SQL query.")
                else:
                    with st.spinner("Executing direct query..."):
                        results = execute_query_on_db(
                            direct_sql_query,
                            db_path=st.session_state.db_path_for_query,
                            current_table_name_for_error_msg=st.session_state.current_table_name
                        )
                    if isinstance(results, pd.DataFrame):
                        st.write("### 📊 Direct Query Results:")
                        st.dataframe(results, use_container_width=True)
                        csv_export = results.to_csv(index=False).encode('utf-8')
                        st.download_button(
                            label="📥 Download Direct Query Results as CSV",
                            data=csv_export, file_name="direct_query_results.csv",
                            mime="text/csv", key="download_direct_results_btn_corrected"
                        )
                    else:
                        st.error(f"Direct Query Execution Failed: {results}")
    
    elif uploaded_file: 
        st.info("Processing uploaded file. Please wait...")
    else:
        st.info("👋 Welcome! Please upload a CSV or XLSX file to begin.")

if __name__ == "__main__":
    main()
