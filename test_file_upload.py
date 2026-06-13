"""Simple test for Feishu file upload capability.

Usage:
    cd /home/liucai/Vibe-Trading
    PYTHONPATH=/home/liucai/Vibe-Trading python test_file_upload.py

Expected output if upload works:
    [FeishuClient] uploaded file: file_v2_xxx...
    ✅ Upload succeeded! File key: file_v2_xxx...

Expected output if upload fails (no permission):
    [FeishuClient] upload failed: {'code': 99991672, 'msg': 'no permission', ...}
    ❌ Upload failed: no permission. Please apply for 'im:resource:upload' in Feishu admin.
"""

import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env", override=True)

sys.path.insert(0, str(_PROJECT_ROOT))

from feishu_bot.client import FeishuClient


def test_upload():
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()

    if not app_id or not app_secret:
        print("❌ FEISHU_APP_ID or FEISHU_APP_SECRET not set in .env")
        return False

    # Create a temporary test file
    test_file = _PROJECT_ROOT / "test_upload_sample.txt"
    test_file.write_text(
        "这是飞书文件上传测试。\n"
        "如果这条消息能成功上传到飞书，说明 im:resource:upload 权限已开通。\n",
        encoding="utf-8",
    )

    client = FeishuClient(app_id, app_secret)

    print(f"📝 Created test file: {test_file}")
    print("🚀 Uploading to Feishu...")

    try:
        file_key = client.upload_file(str(test_file))
        if file_key:
            print(f"✅ Upload succeeded! File key: {file_key}")
            return True
        else:
            print("❌ Upload failed: no file_key returned.")
            return False
    except Exception as e:
        print(f"❌ Upload failed with exception: {e}")
        # Try to print response body for more details
        import requests
        if hasattr(e, 'response') and e.response is not None:
            try:
                body = e.response.json()
                print(f"📄 Feishu error response: {json.dumps(body, ensure_ascii=False, indent=2)}")
            except Exception:
                print(f"📄 Feishu raw response: {e.response.text[:500]}")
        return False
    finally:
        # Clean up
        if test_file.exists():
            test_file.unlink()
            print(f"🧹 Cleaned up test file.")


if __name__ == "__main__":
    ok = test_upload()
    sys.exit(0 if ok else 1)
