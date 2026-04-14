import os
import shutil
import re

TARGET_DIR = r"Y:\Ready Jobs"
ALLOWED_PATTERN = re.compile(r'DELIVERY SHEET|ASSEMBLY SHEET|PLANS & ELEVATIONS', re.IGNORECASE)

def cleanup_unapproved_dark_mode_files():
    deleted_files = 0
    deleted_dirs = 0

    print(f"Scanning {TARGET_DIR} for DARK MODE cleanup...")
    
    # We walk bottom-up so we can delete empty directories safely
    for root, dirs, files in os.walk(TARGET_DIR, topdown=False):
        if os.path.basename(root).upper() == "DARK MODE":
            for file in files:
                if file.lower().endswith('.pdf'):
                    if not ALLOWED_PATTERN.search(file):
                        file_path = os.path.join(root, file)
                        try:
                            os.remove(file_path)
                            print(f"[DELETED] {file_path}")
                            deleted_files += 1
                        except Exception as e:
                            print(f"[ERROR] Failed to delete {file_path}: {e}")
            
            # Check if directory is now empty
            if not os.listdir(root):
                try:
                    os.rmdir(root)
                    print(f"[DELETED DIR] {root}")
                    deleted_dirs += 1
                except Exception as e:
                    print(f"[ERROR] Failed to delete empty directory {root}: {e}")

    print("\n--- Cleanup Complete ---")
    print(f"Deleted {deleted_files} unapproved dark mode files.")
    print(f"Deleted {deleted_dirs} empty dark mode folders.")

if __name__ == "__main__":
    cleanup_unapproved_dark_mode_files()
