import re

with open("ready_jobs_watcher/pdf_dark_mode.py", "r") as f:
    content = f.read()

# Fix the split that got messed up in patch3 due to escaping
content = content.replace("replace('/', '\')", "replace('/', '\\\\')")
content = content.replace("split('\')", "split('\\\\')")

# Fix multiple blank lines
content = re.sub(r'\n{3,}', '\n\n', content)

with open("ready_jobs_watcher/pdf_dark_mode.py", "w") as f:
    f.write(content)
