import re

file_path = 'automated_survey_flask/conversation_relay_view.py'
with open(file_path, 'r') as f:
    content = f.read()

# 1. Modify _topics_runner to use an event
content = content.replace("    def _topics_runner(history_snapshot):", "    topics_ready = threading.Event()\n\n    def _topics_runner(history_snapshot):")
content = content.replace("            state['topics'] = topics", "            state['topics'] = topics\n            topics_ready.set()")

# 2. Reset the event when starting the thread
content = content.replace("            threading.Thread(", "            topics_ready.clear()\n            threading.Thread(")

# 3. Wait for the event before calling _engagement_topics
old_ext_logic = """        use_extension = (mode == 'by_time' and next_q_num > max_q)
        if use_extension:
            eng = _engagement_topics(state)"""

new_ext_logic = """        use_extension = (mode == 'by_time' and next_q_num > max_q)
        if use_extension:
            # Wait up to 1.5s for the topic extraction to finish so we have
            # fresh engagement topics for the LLM.
            topics_ready.wait(timeout=1.5)
            eng = _engagement_topics(state)"""

if old_ext_logic in content:
    content = content.replace(old_ext_logic, new_ext_logic)
    with open(file_path, 'w') as f:
        f.write(content)
    print("Replaced successfully")
else:
    print("Failed to find extension logic")
