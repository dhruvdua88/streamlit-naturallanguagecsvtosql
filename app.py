import streamlit as st
import pandas as pd
import sqlite3
from google import genai
import re

# Configure Google Generative AI API
api_key = "<ENTER KEY HERE>"  # Replace with your actual API key

# Function to convert CSV to SQLite database
def csv_to_sqlite(csv_file, db_name="data.db", table_name="transactions"):
    df = pd.read_csv(csv_file)
    conn = sqlite3.connect(db_name)
    df.to_sql(table_name, conn, if_exists="replace", index=False)
    conn.close()
    return df.columns.tolist()  # Return column headers

# Function to generate SQL query using Google Gemini API
def generate_sql_query(prompt, headers):
    client = genai.Client(api_key=api_key)
    model_id="gemini-2.5-flash-preview-04-17"
    context = f"The CSV file has the following headers: {', '.join(headers)}. ONLY provide the SQL script which can be directly used in the SQLite database based on the '{prompt} also note that the name of the table is transactions. DO NOT DEVIATE THE TABLE NAME '"
    response = client.models.generate_content(model=model_id,contents=context)
    # Extract raw text from the response
    raw_response = response.text.strip()
    
    # Use regex to extract the SQL query from the response
    sql_query_match = re.search(r"```sql\n(.*?)\n```", raw_response, re.DOTALL)
    if sql_query_match:
        sql_query = sql_query_match.group(1).strip()  # Extract the query inside the code block
    else:
        # If no code block is found, assume the entire response is the query
        sql_query = raw_response.strip()
    
    return sql_query

# Function to execute SQL query on SQLite database
def execute_query(query, db_name="data.db"):
    conn = sqlite3.connect(db_name)
    try:
        result_df = pd.read_sql_query(query, conn)
        conn.close()
        return result_df
    except Exception as e:
        conn.close()
        return f"Error executing query: {str(e)}"

# Streamlit App
def main():
    st.title("CSV to SQLite Query Builder 🚀")

    # Step 1: Upload CSV file
    uploaded_file = st.file_uploader("Upload a CSV file", type=["csv"])
    if uploaded_file is not None:
        st.success("CSV file uploaded successfully!")

        # Convert CSV to SQLite DB and get headers
        headers = csv_to_sqlite(uploaded_file)
        st.write("### CSV Headers:")
        st.write(headers)

        # Step 2: Describe headers
        st.write("### Description of Headers:")
        st.write("The uploaded CSV file contains the following columns:")
        for header in headers:
            st.write(f"- **{header}**: Represents data in this column.")

        # Step 3: Ask for a prompt
        prompt = st.text_area("Enter your query prompt (e.g., 'Find all rows where age > 30'):")
        if st.button("Generate SQL Query"):
            if prompt:
                # Step 4: Generate SQL query using Google Gemini API
                sql_query = generate_sql_query(prompt, headers)
                st.write("### Generated SQL Query:")
                st.code(sql_query, language="sql")

                # Step 5: Execute SQL query on SQLite DB
                st.write("### Query Results:")
                results = execute_query(sql_query)
                if isinstance(results, pd.DataFrame):
                    st.dataframe(results)
                else:
                    st.error(results)
            else:
                st.warning("Please enter a prompt to generate an SQL query.")

if __name__ == "__main__":
    main()
