import re

with open("ready_jobs_watcher/pdf_dark_mode.py", "r") as f:
    content = f.read()

# Remove should_use_direct_inversion
content = re.sub(r'def should_use_direct_inversion.*?return "COVER SHEET" in filename_upper\n\n', '', content, flags=re.DOTALL)

# Remove run_direct_inversion
content = re.sub(r'def run_direct_inversion.*?return False\n\n', '', content, flags=re.DOTALL)

# Remove should_invert_images
content = re.sub(r'def should_invert_images.*?return "ISLAND WINGS" in filename_upper or "COVER SHEET" in filename_upper\n', '', content, flags=re.DOTALL)

with open("ready_jobs_watcher/pdf_dark_mode.py", "w") as f:
    f.write(content)
