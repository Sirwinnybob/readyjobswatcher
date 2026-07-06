import os
import shutil
import re

TARGET_DIR = r"Y:\Ready Jobs"
ALLOWED_PATTERN = re.compile(r'DELIVERY SHEET|ASSEMBLY SHEET|PLANS & ELEVATIONS', re.IGNORECASE)

def cleanup_unapproved_dark_mode_files():
    deleted_files = 0
    deleted_dirs = 0

    print(f"Scanning {TARGET_DIR} for DARK MODE cleanup...")
    
    # Use a stack-based traversal with os.scandir for better performance
    stack = [TARGET_DIR]
    dirs_to_process = []

    # Collect all directories
    while stack:
        current_dir = stack.pop()
        dirs_to_process.append(current_dir)
        try:
            with os.scandir(current_dir) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
        except OSError:
            pass
            
    # Process bottom-up
    for dir_path in reversed(dirs_to_process):
        if os.path.basename(dir_path).upper() == "DARK MODE":
            has_remaining_items = False
            try:
                with os.scandir(dir_path) as it:
                    for entry in it:
                        if entry.is_file(follow_symlinks=False):
                            if entry.name.lower().endswith('.pdf') and not ALLOWED_PATTERN.search(entry.name):
                                try:
                                    os.remove(entry.path)
                                    print(f"[DELETED] {entry.path}")
                                    deleted_files += 1
                                except Exception as e:
                                    print(f"[ERROR] Failed to delete {entry.path}: {e}")
                                    has_remaining_items = True
                            else:
                                has_remaining_items = True
                        else:
                            has_remaining_items = True

                # Check if directory is empty based on our iteration above
                if not has_remaining_items:
                    try:
                        os.rmdir(dir_path)
                        print(f"[DELETED DIR] {dir_path}")
                        deleted_dirs += 1
                    except Exception as e:
                        print(f"[ERROR] Failed to delete empty directory {dir_path}: {e}")
            except OSError as e:
                print(f"[ERROR] Failed to process directory {dir_path}: {e}")

    print("\n--- Cleanup Complete ---")
    print(f"Deleted {deleted_files} unapproved dark mode files.")
    print(f"Deleted {deleted_dirs} empty dark mode folders.")

if __name__ == "__main__":
    cleanup_unapproved_dark_mode_files()
