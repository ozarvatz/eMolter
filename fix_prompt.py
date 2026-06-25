from automated_survey_flask import db, prepare_app
from automated_survey_flask.models import LlmPrompt

app = prepare_app()
with app.app_context():
    prompt = LlmPrompt.query.filter_by(lang='he-IL', active=True).first()
    if prompt:
        old_instruction = "[תגובה קצרה ומכילה] + [שאלה אחת לסיום]"
        new_instruction = "[תגובה קצרה ומכילה (משפט אחד בלבד!)] + [שאלה אחת לסיום]"
        
        old_desc = "משימה: תן תגובה קצרה ומכילה למה שנאמר (בטון רגוע ויציב), ואז שאל את שאלה מספר {q_num} מתוך {max_questions}."
        new_desc = "משימה: תן תגובה קצרה ומכילה למה שנאמר (משפט אחד בלבד! אסור לך לפרט או לסכם את דברי המטופל), ואז שאל את שאלה מספר {q_num} מתוך {max_questions}."

        if old_instruction in prompt.system_template:
            prompt.system_template = prompt.system_template.replace(old_instruction, new_instruction)
            prompt.system_template = prompt.system_template.replace(old_desc, new_desc)
            db.session.commit()
            print("Prompt updated successfully.")
        else:
            print("Instruction not found in prompt, or already updated.")
            print(prompt.system_template)
