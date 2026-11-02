import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

results = []

# Test Qt imports
try:
    from ui.base_window import BaseWindow
    results.append("OK: base_window")
except Exception as e:
    results.append(f"FAIL: base_window - {e}")

try:
    from ui.progress_window import ProgressWindow, UPLOAD, DOWNLOAD
    results.append("OK: progress_window")
except Exception as e:
    results.append(f"FAIL: progress_window - {e}")

try:
    from ui.result_window import ResultWindow
    results.append("OK: result_window")
except Exception as e:
    results.append(f"FAIL: result_window - {e}")

try:
    from ui.error_dialog import ErrorDialog
    results.append("OK: error_dialog")
except Exception as e:
    results.append(f"FAIL: error_dialog - {e}")

try:
    from ui.close_button import CloseButton
    results.append("OK: close_button")
except Exception as e:
    results.append(f"FAIL: close_button - {e}")

try:
    from ui.settings_window import SettingsWindow
    results.append("OK: settings_window")
except Exception as e:
    results.append(f"FAIL: settings_window - {e}")

# Test argument parsing
try:
    from core.protocol_handler import parse_args, extract_protocol_code
    from core.buzzheavier_api import extract_code
    args = parse_args(["BZ://h0l6lgmru052"])
    code = extract_protocol_code(args)
    results.append(f"OK: arg parsing - code={code}")
    results.append(f"OK: extract BZ:// -> {extract_code('BZ://h0l6lgmru052')}")
    results.append(f"OK: extract https -> {extract_code('https://buzzheavier.com/h0l6lgmru052')}")
    results.append(f"OK: extract bare -> {extract_code('h0l6lgmru052')}")
except Exception as e:
    results.append(f"FAIL: arg parsing - {e}")

# Test config
try:
    from config.config_manager import load_config, save_config, DEFAULTS
    cfg = load_config()
    results.append(f"OK: config loaded ({len(cfg)} keys)")
except Exception as e:
    results.append(f"FAIL: config - {e}")

# Test file utils
try:
    from utils.file_utils import sanitize_filename, format_size
    results.append(f"OK: format_size(1024) = {format_size(1024)}")
    results.append(f"OK: format_size(1048576) = {format_size(1048576)}")
except Exception as e:
    results.append(f"FAIL: file_utils - {e}")

# Test buzzheavier API URL extraction
try:
    from core.buzzheavier_api import BuzzHeavierAPI, BASE_URL, UPLOAD_HOST
    api = BuzzHeavierAPI()
    results.append(f"OK: BuzzHeavierAPI created (base={BASE_URL}, upload={UPLOAD_HOST})")
except Exception as e:
    results.append(f"FAIL: BuzzHeavierAPI - {e}")

# Test path manager
try:
    from system.path_manager import (
        add_to_path, remove_from_path, is_in_path,
        get_buzzinstaller_dir, get_path_entries, PathError,
    )
    results.append(f"OK: path_manager - dir={get_buzzinstaller_dir()}")
    results.append(f"OK: path_manager - is_in_path={is_in_path()}")
except Exception as e:
    results.append(f"FAIL: path_manager - {e}")

# Resumable bundle modules and pages
try:
    from core import archive, bundle, chunking  # noqa: F401
    from core.bundle_service import BundleDownloadService, BundleUploadService  # noqa: F401
    from core.transfer_db import TransferStore  # noqa: F401
    from core.transfer_lock import TransferGuard  # noqa: F401
    from ui.transfer_widgets import (  # noqa: F401
        ChunkStatusList, InterruptedTransfersCard, TransferProgressCard,
    )
    results.append("OK: resumable bundle modules")
except Exception as e:
    results.append(f"FAIL: resumable bundle modules - {e}")

try:
    from ui.pages.send_page import SendPage  # noqa: F401
    from ui.pages.receive_page import ReceivePage  # noqa: F401
    from ui.pages.settings_page import SettingsPage  # noqa: F401
    results.append("OK: dashboard transfer pages")
except Exception as e:
    results.append(f"FAIL: dashboard transfer pages - {e}")

print("=" * 50)
for r in results:
    print(r)
print("=" * 50)
