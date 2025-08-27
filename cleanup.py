import os

# List of all data files used by the bot
DATA_FILES = [
    'hashtag_data.json',
    'admins.json',
    'admin_nicknames.json',
    'risk_data.json',
    'activity.json',
    'inactive_settings.json',
    'disabled_commands.json',
    'bot.log',
    'bot_debug.log' # From debug_main.py
]

def clean_data_files():
    """
    Deletes all known data and log files to ensure a clean start.
    """
    print("Starting cleanup process...")
    for filename in DATA_FILES:
        if os.path.exists(filename):
            try:
                os.remove(filename)
                print(f"Successfully deleted {filename}")
            except OSError as e:
                print(f"Error deleting file {filename}: {e}")
        else:
            print(f"File not found, skipping: {filename}")
    print("Cleanup process finished.")

if __name__ == '__main__':
    clean_data_files()
