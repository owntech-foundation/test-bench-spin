import os
import re
import sys
import threading


SERIAL_PATTERN = re.compile(r"^[A-Z0-9]{13}$")
PASSPHRASE_PATTERN = re.compile(r"^[A-Za-z0-9]{10}$")


class ProductionError(RuntimeError):
    pass


class ProductionClient:
    def __init__(self):
        self._sdk = None
        self._lock = threading.RLock()

    def _setting(self, name, default=None):
        value = os.getenv(name, default)
        if value is None or not str(value).strip():
            raise ProductionError(f"Missing production setting {name}")
        return str(value).strip()

    def connect(self):
        with self._lock:
            sdk_path = self._setting("OWNTECH_SDK_PATH", "/home/owntech/owntechproductionsdk")
            if sdk_path not in sys.path:
                sys.path.insert(0, sdk_path)
            try:
                from SDK.owntech_prod_sdk import OwntechProdSdk
            except (ImportError, OSError) as error:
                raise ProductionError(f"Unable to load production SDK: {error}") from error

            sdk = OwntechProdSdk(self._setting("OWNTECH_API_BASE_URL"))
            try:
                response = sdk.login(
                    self._setting("OWNTECH_API_USERNAME"),
                    self._setting("OWNTECH_API_PASSWORD"),
                )
            except Exception as error:
                raise ProductionError(f"Production API login failed: {error}") from error
            if response.get("status_code") != 200 or not sdk.token:
                raise ProductionError(f"Production API login returned HTTP {response.get('status_code')}")
            self._sdk = sdk
            return True

    def _connected_sdk(self):
        if self._sdk is None:
            self.connect()
        return self._sdk

    @staticmethod
    def _validated_identity(content):
        serial = str(content.get("serial") or "").strip().upper()
        passphrase = str(content.get("passphrase") or "").strip()
        if not SERIAL_PATTERN.fullmatch(serial):
            raise ProductionError("Production API returned an invalid serial")
        if not PASSPHRASE_PATTERN.fullmatch(passphrase):
            raise ProductionError("Production API returned an invalid passphrase")
        return {"serial": serial, "passphrase": passphrase}

    def generate_identity(self):
        with self._lock:
            sdk = self._connected_sdk()
            try:
                response = sdk.generate_serial(
                    self._setting("OWNTECH_PRODUCT_TYPE", "SPN"),
                    self._setting("OWNTECH_PRODUCT_VERSION", "1.2.1"),
                )
            except Exception as error:
                raise ProductionError(f"Serial generation failed: {error}") from error
            if response.get("status_code") != 200:
                raise ProductionError(f"Serial generation returned HTTP {response.get('status_code')}")
            content = response.get("content") or {}
            return self._validated_identity(content)

    def generate_serial(self):
        """Backward-compatible helper for callers that only need the serial."""
        return self.generate_identity()["serial"]

    def get_passphrase(self, serial):
        normalized_serial = str(serial or "").strip().upper()
        if not SERIAL_PATTERN.fullmatch(normalized_serial):
            raise ProductionError("Cannot retrieve a passphrase without a valid serial")
        with self._lock:
            sdk = self._connected_sdk()
            try:
                response = sdk.list_products(page=1, page_size=20, search=normalized_serial)
            except Exception as error:
                raise ProductionError(f"Passphrase lookup failed: {error}") from error
            if response.get("status_code") != 200:
                raise ProductionError(f"Passphrase lookup returned HTTP {response.get('status_code')}")
            content = response.get("content") or {}
            for item in content.get("items") or []:
                if str(item.get("serial") or "").strip().upper() == normalized_serial:
                    return self._validated_identity(item)["passphrase"]
            raise ProductionError(f"Production API has no identity for serial {normalized_serial}")

    def upload_report(self, serial, report):
        normalized_serial = str(serial or "").strip().upper()
        if not SERIAL_PATTERN.fullmatch(normalized_serial):
            raise ProductionError("Cannot upload a report without a valid serial")
        with self._lock:
            sdk = self._connected_sdk()
            try:
                response = sdk.add_test_report(normalized_serial, report, [])
            except Exception as error:
                raise ProductionError(f"Test report upload failed: {error}") from error
            if response.get("status_code") != 200:
                raise ProductionError(f"Test report upload returned HTTP {response.get('status_code')}")
            return response.get("content")


production_client = ProductionClient()
