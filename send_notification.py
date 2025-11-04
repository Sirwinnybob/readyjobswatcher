import sys
from win10toast import ToastNotifier

if __name__ == "__main__":
    toaster = ToastNotifier()
    title = sys.argv[1] if len(sys.argv) > 1 else "Notification"
    message = sys.argv[2] if len(sys.argv) > 2 else ""
    
    toaster.show_toast(title, message, duration=10, icon_path=None, threaded=False)