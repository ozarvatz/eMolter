import os
import glob
import json

log_files = glob.glob('/home/oz/.gemini/tmp/automated-survey-flask/chats/session-*.jsonl')
if not log_files:
    print("Cost: $0.0000")
    exit(0)

latest_log = max(log_files, key=os.path.getmtime)

input_tokens = 0
output_tokens = 0
cached_tokens = 0

with open(latest_log, 'r') as f:
    for line in f:
        try:
            data = json.loads(line)
            if 'tokens' in data:
                input_tokens += data['tokens'].get('input', 0)
                output_tokens += data['tokens'].get('output', 0)
                cached_tokens += data['tokens'].get('cached', 0)
        except:
            pass

cost = (input_tokens * 2 / 1000000) + (output_tokens * 12 / 1000000) + (cached_tokens * 0.5 / 1000000)
print(f"[💰 Est. Session Cost: ${cost:.4f}]")
