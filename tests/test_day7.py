import agentautopsy
from agentautopsy.db import get_db

agentautopsy.watch()
db = get_db()
print(f"Tables: {db.table_names()}")
print("Day 7 complete — watch() is fully wired")
