import os
import time
from typing import Any

try:
    from gpiozero import Button
except Exception:  # pragma: no cover - falls keine GPIO-Unterstützung vorhanden ist
    Button = None

WINDOW_DEFINITIONS = [
    {"name": "Fenster 1", "gpio": 17},
    {"name": "Fenster 2", "gpio": 27},
    {"name": "Fenster 3", "gpio": 22},
]


def _status_from_environment(sensor_id: int) -> str:
    env_key = f"WINDOW{sensor_id}_STATE"
    value = os.getenv(env_key, "").strip().lower()
    if value in {"closed", "geschlossen", "1", "true", "yes", "on"}:
        return "GESCHLOSSEN"
    if value in {"open", "offen", "0", "false", "no", "off"}:
        return "OFFEN"
    return "OFFEN" if sensor_id % 2 == 0 else "GESCHLOSSEN"


class WindowSensor:
    def __init__(self, name: str, gpio: int, sensor_id: int) -> None:
        self.name = name
        self.gpio = gpio
        self.sensor_id = sensor_id
        self._button = None

        if Button is not None:
            try:
                self._button = Button(gpio, pull_up=True)
            except Exception:
                self._button = None

    def read_state(self) -> dict[str, Any]:
        if self._button is not None:
            is_closed = self._button.is_pressed
            status = "GESCHLOSSEN" if is_closed else "OFFEN"
            detail = "Magnet erkannt" if is_closed else "Kein Magnet"
        else:
            status = _status_from_environment(self.sensor_id)
            detail = "Demo-Modus / GPIO nicht verfügbar"

        return {
            "name": self.name,
            "status": status,
            "detail": detail,
            "gpio": self.gpio,
        }


WINDOW_SENSORS = [
    WindowSensor(config["name"], config["gpio"], index)
    for index, config in enumerate(WINDOW_DEFINITIONS, start=1)
]


def get_window_states() -> list[dict[str, Any]]:
    return [sensor.read_state() for sensor in WINDOW_SENSORS]


def monitor_window_states(interval: float = 0.1) -> None:
    print("========================================")
    print("  Fenster-Status-Überwachung")
    print("  Beenden mit: Strg + C")
    print("========================================\n")

    letzter_zustand = None
    try:
        while True:
            zustände = get_window_states()
            aktueller_zustand = " | ".join(
                f"{entry['name']}: {entry['status']}" for entry in zustände
            )

            if aktueller_zustand != letzter_zustand:
                zeitstempel = time.strftime("%H:%M:%S")
                print(f"[{zeitstempel}] {aktueller_zustand}")
                letzter_zustand = aktueller_zustand

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nProgramm erfolgreich beendet.")


if __name__ == "__main__":
    monitor_window_states()
