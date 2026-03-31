"""
Graphical User Interface Module.

Provides the `SettingsWindow` class for the application, built with `tkinter`.
Allows users to configure backup schedules, Planka integrations, and operation
delays, as well as trigger manual application tasks.
"""
import tkinter as tk
from tkinter import ttk, messagebox
import logging
import threading
import winreg
import re
import sv_ttk
import keyring

# These will be imported from other modules
# from . import main
# from .scheduler import perform_backup
# from .file_handler import manual_scan

# Keyring service name for secure credential storage
KEYRING_SERVICE = "ReadyJobsWatcher"

class SettingsWindow:
    """
    Main configuration interface for Ready Jobs Watcher.

    Provides a scrollable window with sections for Backup Status, Backup Scheduling,
    Planka Integrations, Processing Delays, and Manual Actions. Handles saving and
    validating user input.
    """
    def __init__(self, root, config, app):
        """
        Initialize the SettingsWindow.

        Args:
            root (tk.Tk): The root tkinter window instance.
            config (Config): The application configuration context.
            app (Application): The main application orchestrator context.
        """
        logging.debug("Initializing SettingsWindow")
        self.root = root
        self.config = config
        self.app = app
        self.window = tk.Toplevel(root)
        self.window.title("Ready Jobs Watcher Settings")
        self.window.geometry("400x600")
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.hide_window)

        self.window.update_idletasks()
        width = self.window.winfo_width()
        height = self.window.winfo_height()
        x = (self.window.winfo_screenwidth() // 2) - (width // 2)
        y = (self.window.winfo_screenheight() // 2) - (height // 2)
        self.window.geometry(f'{width}x{height}+{x}+{y}')

        self.window.configure(bg='#2b2b2b' if is_dark_mode() else '#ffffff')

        # Create canvas with scrollbar
        canvas = tk.Canvas(self.window, bg='#2b2b2b' if is_dark_mode() else '#ffffff', highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.window, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas, padding="10")

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Backup Status Section
        ttk.Label(scrollable_frame, text="Backup Status", font=("Segoe UI", 12, "bold")).pack(pady=5)
        self.last_backup_label = ttk.Label(scrollable_frame, text="Last Backup: None", font=("Segoe UI", 10))
        self.last_backup_label.pack()
        self.next_backup_label = ttk.Label(scrollable_frame, text="Next Backup: Calculating...", font=("Segoe UI", 10))
        self.next_backup_label.pack()

        ttk.Label(scrollable_frame, text="Pending Replacements", font=("Segoe UI", 12, "bold")).pack(pady=5)
        self.pending_replacements_label = ttk.Label(scrollable_frame, text="Count: 0", font=("Segoe UI", 10))
        self.pending_replacements_label.pack()

        # Backup Times Section
        ttk.Label(scrollable_frame, text="Backup Times (HH:MM, 24-hour)", font=("Segoe UI", 10, "bold")).pack(pady=5)
        self.time1_entry = ttk.Entry(scrollable_frame, width=15)
        self.time1_entry.insert(0, self.config.BACKUP_TIMES[0])
        self.time1_entry.pack()
        self.time2_entry = ttk.Entry(scrollable_frame, width=15)
        self.time2_entry.insert(0, self.config.BACKUP_TIMES[1])
        self.time2_entry.pack()

        ttk.Button(scrollable_frame, text="Save Schedule", command=self.save_schedule).pack(pady=5)

        # Planka Settings Section
        ttk.Separator(scrollable_frame, orient='horizontal').pack(fill='x', pady=10)
        ttk.Label(scrollable_frame, text="Planka Integration", font=("Segoe UI", 12, "bold")).pack(pady=5)

        ttk.Label(scrollable_frame, text="Base URL:", font=("Segoe UI", 9)).pack(anchor='w', padx=20)
        self.planka_url_entry = ttk.Entry(scrollable_frame, width=35)
        self.planka_url_entry.insert(0, self.config.planka_base_url or "")
        self.planka_url_entry.pack(padx=20)

        ttk.Label(scrollable_frame, text="Username:", font=("Segoe UI", 9)).pack(anchor='w', padx=20, pady=(5,0))
        self.planka_username_entry = ttk.Entry(scrollable_frame, width=35)
        self.planka_username_entry.insert(0, self.config.planka_username or "")
        self.planka_username_entry.pack(padx=20)

        ttk.Label(scrollable_frame, text="Password:", font=("Segoe UI", 9)).pack(anchor='w', padx=20, pady=(5,0))
        self.planka_password_entry = ttk.Entry(scrollable_frame, width=35, show="*")
        # Load password from keyring
        stored_password = get_planka_password(self.config.planka_username)
        if stored_password:
            self.planka_password_entry.insert(0, stored_password)
        self.planka_password_entry.pack(padx=20)

        ttk.Button(scrollable_frame, text="Save Planka Settings", command=self.save_planka_settings).pack(pady=5)

        # Delay Configuration Section
        ttk.Separator(scrollable_frame, orient='horizontal').pack(fill='x', pady=10)
        ttk.Label(scrollable_frame, text="Processing Delays", font=("Segoe UI", 12, "bold")).pack(pady=5)

        ttk.Label(scrollable_frame, text="PDF Conversion Delay (seconds):", font=("Segoe UI", 9)).pack(anchor='w', padx=20)
        self.pdf_delay_entry = ttk.Entry(scrollable_frame, width=15)
        self.pdf_delay_entry.insert(0, str(self.config.pdf_conversion_delay_seconds))
        self.pdf_delay_entry.pack(padx=20)

        ttk.Label(scrollable_frame, text="New Folder Delay (seconds):", font=("Segoe UI", 9)).pack(anchor='w', padx=20, pady=(5,0))
        self.folder_delay_entry = ttk.Entry(scrollable_frame, width=15)
        self.folder_delay_entry.insert(0, str(self.config.new_folder_delay_seconds))
        self.folder_delay_entry.pack(padx=20)

        ttk.Button(scrollable_frame, text="Save Delay Settings", command=self.save_delay_settings).pack(pady=5)

        # Action Buttons Section
        ttk.Separator(scrollable_frame, orient='horizontal').pack(fill='x', pady=10)
        ttk.Button(scrollable_frame, text="Backup Now", command=lambda: threading.Thread(target=self.app.perform_backup, daemon=True).start()).pack(pady=5)
        ttk.Button(scrollable_frame, text="Scan Ready Jobs Now", command=lambda: threading.Thread(target=self.app.initial_scan, daemon=True).start()).pack(pady=5)
        ttk.Button(scrollable_frame, text="Convert PDFs to Dark Mode", command=self.run_dark_mode_conversion).pack(pady=5)
        ttk.Button(scrollable_frame, text="Force Convert All PDFs", command=self.run_dark_mode_conversion_force).pack(pady=5)

        self.window.withdraw()
        self.window.update_idletasks()

    def show_window(self):
        """
        Reveal the GUI window and pause background file processing.
        """
        try:
            logging.debug("Showing GUI window")
            self.app.PAUSE_PROCESSING = True
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
        """
        Hide the GUI window and resume background file processing operations.
        """
        try:
            logging.debug("Hiding GUI window")
            self.app.PAUSE_PROCESSING = False
            logging.info("GUI closed: Resuming file processing.")
            self.window.withdraw()
            threading.Thread(target=self.app.initial_scan, daemon=True).start()
        except Exception as e:
            logging.error(f"Failed to close GUI: {e}")

    def update_status(self):
        """
        Update the labels reflecting application status (like Last Backup time).
        Must be called from main thread or via schedule_update.
        """
        try:
            logging.debug("Updating GUI status")
            if self.app.LAST_BACKUP_TIME:
                self.last_backup_label.config(text=f"Last Backup: {self.app.LAST_BACKUP_TIME.strftime('%Y-%m-%d %H:%M')}")
            else:
                self.last_backup_label.config(text="Last Backup: None")
            next_time = self.config.get_next_backup_time()
            self.next_backup_label.config(text=f"Next Backup: {next_time.strftime('%Y-%m-%d %H:%M')}")
            self.update_pending_replacements_count()
        except Exception as e:
            logging.error(f"Failed to update GUI status: {e}")

    def schedule_update(self):
        """
        Thread-safe method to schedule a GUI update on the main thread.
        """
        try:
            self.window.after(0, self.update_status)
        except Exception as e:
            logging.error(f"Failed to schedule GUI update: {e}")

    def update_pending_replacements_count(self):
        """
        Update the label displaying the number of files awaiting renaming.
        """
        try:
            count = len(self.app.PENDING_RENAMES)
            self.pending_replacements_label.config(text=f"Count: {count}")
        except Exception as e:
            logging.error(f"Failed to update pending replacements count: {e}")

    def update_status_periodic(self):
        """
        Periodically polls and updates application status in the GUI window while open.
        """
        if self.window.winfo_viewable():
            self.update_status()
            self.update_pending_replacements_count()
            self.window.after(60000, self.update_status_periodic)

    def save_schedule(self):
        """
        Validate and save the user's preferred backup scheduling times.
        """
        time1 = self.time1_entry.get().strip()
        time2 = self.time2_entry.get().strip()

        # Pattern for HH:MM format (24-hour time)
        time_pattern = re.compile(r'^\d{1,2}:\d{2}$')

        try:
            logging.debug(f"Saving schedule: {time1}, {time2}")

            # Validate each time entry
            for t in [time1, time2]:
                if t:  # Skip empty entries
                    # Check format with regex
                    if not time_pattern.match(t):
                        messagebox.showerror(
                            "Invalid Format",
                            f"Time '{t}' has invalid format.\nExpected format: HH:MM (e.g., 09:30, 14:00)"
                        )
                        logging.error(f"Time format validation failed for '{t}'")
                        return

                    # Parse and validate hour and minute ranges
                    try:
                        hour, minute = map(int, t.split(':'))
                    except ValueError:
                        messagebox.showerror(
                            "Invalid Format",
                            f"Time '{t}' could not be parsed.\nEnsure hours and minutes are numeric."
                        )
                        logging.error(f"Time parsing failed for '{t}'")
                        return

                    if not (0 <= hour <= 23):
                        messagebox.showerror(
                            "Invalid Hour",
                            f"Hour in '{t}' must be between 0 and 23.\nReceived: {hour}"
                        )
                        logging.error(f"Hour validation failed: {hour} not in range 0-23")
                        return

                    if not (0 <= minute <= 59):
                        messagebox.showerror(
                            "Invalid Minute",
                            f"Minute in '{t}' must be between 0 and 59.\nReceived: {minute}"
                        )
                        logging.error(f"Minute validation failed: {minute} not in range 0-59")
                        return

            # Ensure at least one time is specified
            new_times = [t for t in [time1, time2] if t]
            if not new_times:
                messagebox.showerror(
                    "No Times Specified",
                    "At least one backup time must be specified."
                )
                logging.error("No backup times specified")
                return

            # Save validated times
            self.config.BACKUP_TIMES = new_times
            self.config.save()
            logging.info(f"Backup schedule updated: {self.config.BACKUP_TIMES}")
            messagebox.showinfo("Success", "Backup schedule updated successfully.")
            self.update_status()

        except Exception as e:
            messagebox.showerror("Error", f"Failed to save schedule: {e}")
            logging.error(f"Unexpected error saving schedule: {e}")

    def save_planka_settings(self):
        """
        Validate and save Planka settings with secure password storage via keyring.
        """
        try:
            url = self.planka_url_entry.get().strip()
            username = self.planka_username_entry.get().strip()
            password = self.planka_password_entry.get()

            # Validate URL format
            if url and not (url.startswith('http://') or url.startswith('https://')):
                messagebox.showerror(
                    "Invalid URL",
                    "Planka URL must start with http:// or https://"
                )
                return

            # Update config
            self.config.planka_base_url = url if url else None
            self.config.planka_username = username if username else None

            # Store password securely in Windows Credential Manager
            if username and password:
                set_planka_password(username, password)
                logging.info(f"Planka credentials saved securely for user: {username}")
            elif username:
                # Clear password if username exists but password is empty
                delete_planka_password(username)
                logging.info(f"Planka password cleared for user: {username}")

            self.config.save()
            logging.info("Planka settings saved successfully")
            messagebox.showinfo("Success", "Planka settings saved successfully.\nRestart the application for changes to take effect.")

        except Exception as e:
            logging.error(f"Failed to save Planka settings: {e}")
            messagebox.showerror("Error", f"Failed to save Planka settings: {e}")

    def save_delay_settings(self):
        """
        Validate and save delay configuration settings for background operations.
        """
        try:
            pdf_delay = self.pdf_delay_entry.get().strip()
            folder_delay = self.folder_delay_entry.get().strip()

            logging.debug(f"Saving delay settings: PDF={pdf_delay}s, Folder={folder_delay}s")

            # Validate PDF delay
            try:
                pdf_delay_value = float(pdf_delay)
                if pdf_delay_value < 0:
                    messagebox.showerror(
                        "Invalid Value",
                        "PDF conversion delay must be a non-negative number."
                    )
                    logging.error(f"PDF delay validation failed: {pdf_delay_value} < 0")
                    return
            except ValueError:
                messagebox.showerror(
                    "Invalid Format",
                    f"PDF conversion delay '{pdf_delay}' is not a valid number."
                )
                logging.error(f"PDF delay parsing failed for '{pdf_delay}'")
                return

            # Validate folder delay
            try:
                folder_delay_value = float(folder_delay)
                if folder_delay_value < 0:
                    messagebox.showerror(
                        "Invalid Value",
                        "New folder delay must be a non-negative number."
                    )
                    logging.error(f"Folder delay validation failed: {folder_delay_value} < 0")
                    return
            except ValueError:
                messagebox.showerror(
                    "Invalid Format",
                    f"New folder delay '{folder_delay}' is not a valid number."
                )
                logging.error(f"Folder delay parsing failed for '{folder_delay}'")
                return

            # Save validated delays
            self.config.pdf_conversion_delay_seconds = pdf_delay_value
            self.config.new_folder_delay_seconds = folder_delay_value
            self.config.save()
            logging.info(f"Delay settings updated: PDF={pdf_delay_value}s, Folder={folder_delay_value}s")
            messagebox.showinfo("Success", "Delay settings updated successfully.\nNew delays will be used for future operations.")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to save delay settings: {e}")
            logging.error(f"Unexpected error saving delay settings: {e}")

    def run_dark_mode_conversion(self):
        """
        Trigger an asynchronous PDF dark mode conversion task.
        """
        try:
            from .pdf_dark_mode import run_dark_mode_conversion_async
            logging.info("User triggered PDF dark mode conversion from GUI")
            run_dark_mode_conversion_async(dry_run=False, theme="classic", invert_images=True)
            messagebox.showinfo("PDF Dark Mode Conversion", "PDF dark mode conversion started in background.\nImages will be inverted for Island Wings and COVER SHEET PDFs.\n\nCheck logs for progress.")
        except Exception as e:
            logging.error(f"Failed to trigger PDF dark mode conversion: {e}")
            messagebox.showerror("Error", f"Failed to start PDF dark mode conversion: {e}")

    def run_dark_mode_conversion_force(self):
        """
        Force convert all PDFs to dark mode, ignoring modification dates and bypassing skips.
        """
        try:
            # Confirm with user since this will reconvert ALL PDFs
            result = messagebox.askyesno(
                "Force Convert All PDFs",
                "This will reconvert ALL PDFs to dark mode, even if they were already converted.\n\n"
                "This may take a while and will overwrite existing dark mode versions.\n\n"
                "Continue?"
            )

            if result:
                from .pdf_dark_mode import run_dark_mode_conversion_async
                logging.info("User triggered FORCE PDF dark mode conversion from GUI")
                run_dark_mode_conversion_async(dry_run=False, theme="classic", force=True, invert_images=True)
                messagebox.showinfo("Force Convert All PDFs", "Force conversion started in background.\nAll PDFs will be reconverted.\nImages will be inverted for Island Wings and COVER SHEET PDFs.\n\nCheck logs for progress.")
        except Exception as e:
            logging.error(f"Failed to trigger force PDF dark mode conversion: {e}")
            messagebox.showerror("Error", f"Failed to start force conversion: {e}")

def is_dark_mode():
    """
    Determine if the Windows OS is currently using a Dark Mode theme.

    Returns:
        bool: True if OS is in Dark Mode, False otherwise.
    """
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize")
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return value == 0
    except Exception:
        return False

# Secure credential storage functions using Windows Credential Manager
def get_planka_password(username: str) -> str:
    """
    Retrieve a Planka password from the Windows Credential Manager.

    Args:
        username (str): The Planka username to look up.

    Returns:
        str: The retrieved password, or an empty string if not found.
    """
    if not username:
        return ""
    try:
        return keyring.get_password(KEYRING_SERVICE, username) or ""
    except Exception as e:
        logging.error(f"Failed to retrieve Planka password from keyring: {e}")
        return ""

def set_planka_password(username: str, password: str) -> None:
    """
    Securely store a Planka password in the Windows Credential Manager.

    Args:
        username (str): Planka account username.
        password (str): Planka account password.
    """
    try:
        keyring.set_password(KEYRING_SERVICE, username, password)
    except Exception as e:
        logging.error(f"Failed to store Planka password in keyring: {e}")
        raise

def delete_planka_password(username: str) -> None:
    """
    Remove a stored Planka password from the Windows Credential Manager.

    Args:
        username (str): The username of the password to delete.
    """
    try:
        keyring.delete_password(KEYRING_SERVICE, username)
    except keyring.errors.PasswordDeleteError:
        # Password doesn't exist, which is fine
        pass
    except Exception as e:
        logging.error(f"Failed to delete Planka password from keyring: {e}")
