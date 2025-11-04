# OBSOLETE: This script is outdated and its functionality has been integrated into ready_jobs_watcher.py

import os
import time

# Configuration
ROOT_DIR = r'Y:\Ready Jobs'
CNC_SUBDIR = 'CNC'

def extract_job_number(folder_name):
    # Extract the job number, which is everything before the first " - "
    if ' - ' in folder_name:
        return folder_name.split(' - ', 1)[0]
    return None  # Invalid folder name

def is_job_folder(folder_path):
    folder_name = os.path.basename(folder_path)
    return extract_job_number(folder_name) is not None

def process_file(file_path, job_num, dir_path):
    if not os.path.isfile(file_path):
        return

    original_name = os.path.basename(file_path)
    if original_name.startswith(job_num + ' - '):
        return  # Already renamed

    new_name = job_num + ' - ' + original_name
    new_path = os.path.join(dir_path, new_name)

    try:
        os.rename(file_path, new_path)
        print(f"Renamed: {file_path} -> {new_path}")
    except PermissionError:
        print(f"File locked: {file_path}. Skipping.")
    except Exception as e:
        print(f"Error renaming {file_path}: {e}")

def process_cnc_folder(job_folder):
    if not os.path.isdir(job_folder) or not is_job_folder(job_folder):
        return

    job_num = extract_job_number(os.path.basename(job_folder))

    # Process files in CNC subfolder only
    cnc_path = os.path.join(job_folder, CNC_SUBDIR)
    if os.path.isdir(cnc_path):
        for item in os.listdir(cnc_path):
            full_path = os.path.join(cnc_path, item)
            if os.path.isfile(full_path):
                process_file(full_path, job_num, cnc_path)

def main():
    # Scan all job folders and process CNC subfolders
    for folder in os.listdir(ROOT_DIR):
        full_path = os.path.join(ROOT_DIR, folder)
        process_cnc_folder(full_path)

if __name__ == "__main__":
    print(f"Processing CNC files in {ROOT_DIR}...")
    main()
    print("Processing complete.")
