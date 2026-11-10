import uuid
import json
import os
from datetime import datetime, timedelta
import threading
import inspect

class ContextManager:
   def __init__(self):
       self.root_id = None
       self.context_dict = {}
       self.log_file_path = None
       self.lock = threading.Lock()  # Safe for concurrent writes in same process

   def start_root(self, testCaseId):
       self.root_id = f"{testCaseId}_{uuid.uuid4().hex[:6]}"
       os.makedirs("logs", exist_ok=True)
       self.log_file_path = f"logs/context_log_{self.root_id}.json"
       with open(self.log_file_path, "w") as f:
           json.dump({"root_id": self.root_id, "log": []}, f, indent=2)

   def get_root(self):
       return self.root_id

   def set(self, key, value):
       self.context_dict[(self.root_id, key)] = value
       self._log_operation("set", key, value)

   def get(self, key):
       value = self.context_dict.get((self.root_id, key))
       self._log_operation("get", key, value)
       return value

   def dump(self):
       return {k: v for k, v in self.context_dict.items() if k[0] == self.root_id}

   def _log_operation(self, action, key, value):
       log_entry = {
           "timestamp": datetime.now().isoformat(),
           "action": action,
           "key": key,
           "value": value,
           "root_id": self.root_id,
           "caller": self._get_caller()
       }
       if self.log_file_path:
           with self.lock:
               try:
                   with open(self.log_file_path, "r+", encoding="utf-8") as f:
                       data = json.load(f)
                       data["log"].append(log_entry)
                       f.seek(0)
                       json.dump(data, f, indent=2)
                       f.truncate()
               except Exception as e:
                   print(f"[ContextManager] Logging error: {e}")

   def _get_caller(self):
       stack = inspect.stack()
       # Skip current and _log_operation calls
       for frame in stack[2:]:
           if "self" in frame.frame.f_locals:
               return {
                   "function": frame.function,
                   "file": os.path.basename(frame.filename),
                   "line": frame.lineno
               }
       return {"function": "unknown", "file": "unknown", "line": 0}


   def populateDictionary(self):
       print("\npopulateDictionary")
       self.set('date1', (datetime.now()-timedelta(50)).strftime("%Y-%m-%d"))
       self.set('date2', (datetime.now()-timedelta(70)).strftime("%Y-%m-%d"))
       self.set('date3', (datetime.now()-timedelta(95)).strftime("%Y-%m-%d"))


       print(f"self.dump:\n {self.dump()}")
   

