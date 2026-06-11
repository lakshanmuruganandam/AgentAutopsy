import sqlite3

db_path = "/Users/lakshanmuruganandam/Desktop/AgentAutopsy/AgentAutopsy_Repo/agentautopsy.db"
conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute('''
    CREATE TABLE IF NOT EXISTS fixes (
        error_type TEXT,
        message TEXT,
        suggestion TEXT,
        applied INTEGER DEFAULT 0
    )
''')

suggestion = """\033[38;5;39m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m
\033[1;38;5;82m✅ [AgentAutopsy] Auto-Fix Suggested\033[0m
\033[38;5;244mThe LLM returned plain text, but you attempted to parse it as JSON.\033[0m
\033[38;5;244mChange your prompt on line 23 to:\033[0m
\033[1;37m"Ensure strict JSON compliance by returning ONLY valid JSON."\033[0m
\033[38;5;39m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m
"""

error_type = "JSONDecodeError"
message = "Expecting value: line 1 column 1 (char 0)"

cur.execute("DELETE FROM fixes WHERE error_type = ?", (error_type,))
cur.execute("INSERT INTO fixes (error_type, message, suggestion, applied) VALUES (?, ?, ?, ?)", 
            (error_type, message, suggestion, 0))

conn.commit()
conn.close()
print("Fix seeded!")
