import datetime
import threading
from collections import deque


TEST_STEPS = (
    ("start", "Start test", "Wait for the operator to press START."),
    ("pi", "Check Raspberry Pi", "Confirm that the controller service responds."),
    ("network", "Check network", "Verify connectivity to owntech.org."),
    ("stlink", "Reset ST-LINK", "Reset the programmer before accessing the DUT."),
    ("shorts", "Short-circuit test", "Inspect the DUT pin signature for shorts."),
    ("current_1", "Initial current", "Measure current before programming."),
    ("ldo", "LDO voltage", "Measure the DUT 3.3 V rail."),
    ("option_bytes", "Program option bytes", "Configure and verify STM32 option bytes."),
    ("test_firmware", "Program test firmware", "Flash the DUT through ST-LINK."),
    ("current_2", "Programmed current", "Measure current with the test firmware."),
    ("sample_firmware", "Program sample firmware", "Upload through MCUboot over USB."),
    ("finish", "Finish", "Stop logging and remove the tested board."),
)

OPERATOR_STEPS = (
    "Place the SPIN in the yellow jaws and latch the red lever.",
    "Press the green START button.",
    "Follow the instructions on the tester display.",
    "When requested, release the lever and remove the SPIN from the fixture.",
    "Connect the same SPIN to the USB-C cable and press the green button.",
    "Wait for the success indication; programming can take several minutes.",
    "Disconnect the tested SPIN and insert the next board.",
)

STEP_INDEX = {step[0]: index for index, step in enumerate(TEST_STEPS)}


def _timestamp():
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")


class TestStatus:
    def __init__(self):
        self._lock = threading.Lock()
        self._events = deque(maxlen=30)
        self._service_started_at = _timestamp()
        self._controller_connected = False
        self._controller_port = None
        self._test_state = "idle"
        self._current_step = 0
        self._progress = None
        self._test_started_at = None
        self._test_finished_at = None
        self._last_event_at = None
        self._last_message = None
        self._failed_steps = set()
        self._measurements = {}

    def set_controller(self, port):
        with self._lock:
            self._controller_connected = port is not None
            self._controller_port = port
            text = "Controller connected" if port else "Controller disconnected"
            if port:
                text += f" on {port}"
            self._append_event(text, "system")

    def record(self, message, direction="received"):
        message = message.strip()
        if not message:
            return

        with self._lock:
            self._last_message = message
            self._last_event_at = _timestamp()
            self._append_event(message, direction)
            self._update_from_message(message)

    def _append_event(self, message, direction):
        event_time = _timestamp()
        self._last_event_at = event_time
        self._events.appendleft({"time": event_time, "direction": direction, "message": message})

    def _activate(self, step_id):
        self._current_step = STEP_INDEX[step_id]
        self._progress = None

    def _update_from_message(self, message):
        if message == "T_STR":
            self._test_state = "running"
            self._current_step = 0
            self._progress = None
            self._test_started_at = _timestamp()
            self._test_finished_at = None
            self._failed_steps.clear()
            self._measurements.clear()
            return

        command_steps = {
            "C_PRP": "pi",
            "C_PIN": "network",
            "C_SLR": "stlink",
            "C_OBP": "option_bytes",
            "C_TFP": "test_firmware",
            "C_SFP": "sample_firmware",
        }
        if message in command_steps:
            self._activate(command_steps[message])

        test_steps = {
            "T_SHC": "shorts",
            "T_CU1": "current_1",
            "T_LDO": "ldo",
            "T_OBP": "option_bytes",
            "T_TFP": "test_firmware",
            "T_CU2": "current_2",
            "T_SFP": "sample_firmware",
        }
        for prefix, step_id in test_steps.items():
            if message.startswith(prefix):
                self._activate(step_id)
                if message.endswith("_TKO"):
                    self._failed_steps.add(step_id)
                    self._test_state = "failed"
                elif message.endswith("_SUC") or (" " in message and not message.startswith("T_SHC_LST")):
                    self._failed_steps.discard(step_id)
                    self._test_state = "running"
                self._record_measurement(prefix, message)
                break

        response_steps = {
            "R_PIN": "network",
            "R_SLR": "stlink",
            "R_OBP": "option_bytes",
            "R_TFP": "test_firmware",
            "R_SFP": "sample_firmware",
        }
        for prefix, step_id in response_steps.items():
            if message.startswith(prefix):
                if message.endswith(("_TKO", "_FND", "_FPM")):
                    self._failed_steps.add(step_id)
                    self._test_state = "failed"
                elif message.endswith("_SUC"):
                    self._failed_steps.discard(step_id)
                    self._test_state = "running"
                break

        if message.startswith(("U_TFP_PER ", "U_SFP_PER ")):
            try:
                self._progress = max(0, min(100, int(message.rsplit(" ", 1)[1])))
            except ValueError:
                self._progress = None

        if message == "T_STP":
            self._activate("finish")
            self._test_state = "failed" if self._failed_steps else "passed"
            self._progress = 100
            self._test_finished_at = _timestamp()

    def _record_measurement(self, prefix, message):
        if prefix not in ("T_CU1", "T_CU2", "T_LDO") or " " not in message:
            return
        value = message.split(" ", 1)[1]
        unit = "V" if prefix == "T_LDO" else "mA"
        self._measurements[prefix] = f"{value} {unit}"

    def snapshot(self):
        with self._lock:
            steps = []
            for index, (step_id, title, description) in enumerate(TEST_STEPS):
                if step_id in self._failed_steps:
                    state = "failed"
                elif self._test_state in ("passed", "failed") and index <= self._current_step:
                    state = "complete" if self._test_state == "passed" else "pending"
                elif index < self._current_step:
                    state = "complete"
                elif index == self._current_step:
                    state = "active" if self._test_state in ("running", "failed") else "pending"
                else:
                    state = "pending"
                steps.append({"id": step_id, "title": title, "description": description, "state": state})

            return {
                "service": {"state": "running", "started_at": self._service_started_at},
                "controller": {"connected": self._controller_connected, "port": self._controller_port},
                "test": {
                    "state": self._test_state,
                    "current_step": self._current_step,
                    "progress": self._progress,
                    "started_at": self._test_started_at,
                    "finished_at": self._test_finished_at,
                },
                "last_event_at": self._last_event_at,
                "last_message": self._last_message,
                "measurements": dict(self._measurements),
                "steps": steps,
                "operator_steps": list(OPERATOR_STEPS),
                "events": list(self._events),
            }


status = TestStatus()
