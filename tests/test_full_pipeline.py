import agentautopsy
from agentautopsy.db import get_db, insert_event

agentautopsy.watch()
db = get_db()

from agentautopsy.db import get_db, create_tables, insert_run

runs = list(db["runs"].rows)
run_id = runs[-1]["id"]
insert_event(db, run_id, "llm_call", {"model": "gpt-4", "messages": [{"role": "user", "content": "fetch data"}]})
insert_event(db, run_id, "error", {"error_type": "TimeoutError", "message": "request timed out after 30s"})
print("Pipeline test complete — check output above on exit")
