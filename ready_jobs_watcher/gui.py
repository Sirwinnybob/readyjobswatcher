import tkinter as tk
from tkinter import ttk, messagebox
import logging
import threading
import winreg
import sv_ttk

# These will be imported from other modules
# from . import main
# from .scheduler import perform_backup
# from .file_handler import manual_scan

class SettingsWindow:
    def __init__(self, root, config, app):
        logging.debug("Initializing SettingsWindow")
        self.root = root
        self.config = config
        self.app = app
        self.window = tk.Toplevel(root)
        self.window.title("Ready Jobs Watcher Settings")
        self.window.geometry("300x400")
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.hide_window)

        self.window.update_idletasks()
        width = self.window.winfo_width()
        height = self.window.winfo_height()
        x = (self.window.winfo_screenwidth() // 2) - (width // 2)
        y = (self.window.winfo_screenheight() // 2) - (height // 2)
        self.window.geometry(f'{width}x{height}+{x}+{y}')

        self.window.configure(bg='#2b2b2b' if is_dark_mode() else '#ffffff')

        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main_frame, text="Backup Status", font=("Segoe UI", 12, "bold")).pack(pady=5)
        self.last_backup_label = ttk.Label(main_frame, text="Last Backup: None", font=("Segoe UI", 10))
        self.last_backup_label.pack()
        self.next_backup_label = ttk.Label(main_frame, text="Next Backup: Calculating...", font=("Segoe UI", 10))
        self.next_backup_label.pack()

        ttk.Label(main_frame, text="Pending Replacements", font=("Segoe UI", 12, "bold")).pack(pady=5)
        self.pending_replacements_label = ttk.Label(main_frame, text="Count: 0", font=("Segoe UI", 10))
        self.pending_replacements_label.pack()

        ttk.Label(main_frame, text="Backup Times (HH:MM, 24-hour)", font=("Segoe UI", 10, "bold")).pack(pady=5)
        self.time1_entry = ttk.Entry(main_frame, width=10)
        self.time1_entry.insert(0, self.config.BACKUP_TIMES[0])
        self.time1_entry.pack()
        self.time2_entry = ttk.Entry(main_frame, width=10)
        self.time2_entry.insert(0, self.config.BACKUP_TIMES[1])
        self.time2_entry.pack()

        ttk.Button(main_frame, text="Save Schedule", command=self.save_schedule).pack(pady=5)
        ttk.Button(main_frame, text="Backup Now", command=lambda: threading.Thread(target=self.app.perform_backup, daemon=True).start()).pack(pady=5)
        ttk.Button(main_frame, text="Scan Ready Jobs Now", command=lambda: threading.Thread(target=self.app.initial_scan, daemon=True).start()).pack(pady=5)

        self.window.withdraw()
        self.window.update_idletasks()

    def show_window(self):
        # global PAUSE_PROCESSING
        try:
            logging.debug("Showing GUI window")
            # PAUSE_PROCESSING = True
            logging.info("GUI opened: Pausing file processing.")
            self.update_status()
            self.window.deiconify()
            self.window.update_idletasks()
            self.window.update()
            self.window.after(60000, self.update_status_periodic)
        except Exception as e:
            logging.error(f"Failed to open GUI: {e}")
            messagebox.showerror("Error", "Failed to open settings window.")

    def hide_window(self):
        # global PAUSE_PROCESSING
        try:
            logging.debug("Hiding GUI window")
            # PAUSE_PROCESSING = False
            logging.info("GUI closed: Resuming file processing.")
            self.window.withdraw()
            # threading.Thread(target=manual_scan, args=(self.config,), daemon=True).start()
        except Exception as e:
            logging.error(f"Failed to close GUI: {e}")

    def update_status(self):
        # global LAST_BACKUP_TIME
        try:
            logging.debug("Updating GUI status")
            # if LAST_BACKUP_TIME:
            #     self.last_backup_label.config(text=f"Last Backup: {LAST_BACKUP_TIME.strftime('%Y-%m-%d %H:%M')}")
            # else:
            #     self.last_backup_label.config(text="Last Backup: None")
            next_time = self.config.get_next_backup_time()
            self.next_backup_label.config(text=f"Next Backup: {next_time.strftime('%Y-%m-%d %H:%M')}")
            self.update_pending_replacements_count()
        except Exception as e:
            logging.error(f"Failed to update GUI status: {e}")

    def update_pending_replacements_count(self):
        # global PENDING_RENAMES
        try:
            # count = len(PENDING_RENAMES)
            # self.pending_replacements_label.config(text=f"Count: {count}")
            pass
        except Exception as e:
            logging.error(f"Failed to update pending replacements count: {e}")

    def update_status_periodic(self):
        if self.window.winfo_viewable():
            self.update_status()
            self.update_pending_replacements_count()
            self.window.after(60000, self.update_status_periodic)

    def save_schedule(self):
        time1 = self.time1_entry.get()
        time2 = self.time2_entry.get()
        try:
            logging.debug(f"Saving schedule: {time1}, {time2}")
            for t in [time1, time2]:
                if t:
                    hour, minute = map(int, t.split(':'))
                    if not (0 <= hour <= 23 and 0 <= minute <= 59):
                        raise ValueError
            new_times = [t for t in [time1, time2] if t]
            if not new_times:
                raise ValueError("At least one backup time must be specified.")
            self.config.BACKUP_TIMES = new_times
            self.config.save()
            logging.info(f"Backup schedule updated: {self.config.BACKUP_TIMES}")
            messagebox.showinfo("Success", "Backup schedule updated.")
            self.update_status()
        except ValueError:
            messagebox.showerror("Error", "Invalid time format. Use HH:MM (24-hour, e.g., 14:30).")
            logging.error(f"Failed to update backup schedule: Invalid format for {time1}, {time2}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save schedule: {e}")
            logging.error(f"Failed to save schedule: {e}")

def is_dark_mode():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize")
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return value == 0
    except:
        return False
