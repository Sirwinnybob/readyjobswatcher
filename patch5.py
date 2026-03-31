with open("ready_jobs_watcher/pdf_dark_mode.py", "r") as f:
    content = f.read()

content = content.replace("replace('/', '\\')", "replace('/', '\\\\')")
content = content.replace("split('\\')", "split('\\\\')")

with open("ready_jobs_watcher/pdf_dark_mode.py", "w") as f:
    f.write(content)
