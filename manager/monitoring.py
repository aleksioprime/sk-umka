"""Чтение и запись параметров MQTT в каталоге приложения на плате."""

import fcntl
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

from sorter.launch_config import load, parse_bool

CONFIG = Path("umka.conf")
BROKER_KEYS = ("mqtt_host", "mqtt_port", "mqtt_tls", "mqtt_username")


def current_config():
    # Содержимое файла настроек никогда не должно попадать в вывод менеджера.
    logging.getLogger("sorter.launch_config").disabled = True
    return load(CONFIG)


def show():
    settings = current_config()
    values = (
        settings.get("device_id", ""),
        settings.get("mqtt_host") or "mqtt.skolhub.ru",
        settings.get("mqtt_port", 8883),
        "yes" if settings.get("mqtt_tls", True) else "no",
        settings.get("mqtt_username") or "umka",
        "yes" if settings.get("mqtt_password") else "no",
    )
    print(*values, sep="\n")


def read_values():
    fields = sys.stdin.read().split("\n")
    if len(fields) != 7 or fields[-1]:
        raise ValueError("Настройки переданы не полностью.")
    device_id, host, port, tls, username, password, _ = fields
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", device_id):
        raise ValueError("ID должен содержать от 1 до 64 латинских букв, цифр, дефисов или подчёркиваний.")
    if not re.fullmatch(r"[A-Za-z0-9._:%-]+", host):
        raise ValueError("Укажите имя или IP-адрес MQTT-брокера без схемы и порта.")
    if not re.fullmatch(r"[0-9]{1,5}", port) or not 1 <= int(port) <= 65535:
        raise ValueError("Порт MQTT должен быть числом от 1 до 65535.")
    try:
        use_tls = parse_bool(tls)
    except ValueError:
        raise ValueError("Для TLS введите yes или no.") from None
    if not username or any(
        value != value.strip() or "#" in value or "\0" in value
        or (value and value.splitlines() != [value])
        for value in (username, password)
    ):
        raise ValueError("Логин обязателен. Логин и пароль должны быть без #, переносов и пробелов по краям.")

    return {
        "device_id": device_id,
        "mqtt_host": host,
        "mqtt_port": int(port),
        "mqtt_tls": use_tls,
        "mqtt_username": username,
        "mqtt_password": password,
    }


def save(updates):
    # Блокировка каталога остаётся действительной после атомарной замены файла.
    directory = os.open(CONFIG.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(directory, fcntl.LOCK_EX)
        current = {"mqtt_port": 8883, "mqtt_tls": True, **current_config()}
        # Старый пароль подходит только для того же подключения.
        if not updates["mqtt_password"] and all(
            updates[key] == current.get(key) for key in BROKER_KEYS
        ):
            updates["mqtt_password"] = current.get("mqtt_password", "")
        original = CONFIG.read_text(encoding="utf-8") if CONFIG.exists() else ""
        lines = [
            line for line in original.splitlines()
            if "=" not in line or line.partition("=")[0].strip() not in updates
        ]
        if lines and lines[-1]:
            lines.append("")
        values = {**updates, "mqtt_tls": "yes" if updates["mqtt_tls"] else "no"}
        lines.extend(f"{key} = {value}" for key, value in values.items())

        with tempfile.NamedTemporaryFile(
            mode="w", dir=CONFIG.parent, prefix=".umka-conf-", encoding="utf-8", delete=False,
        ) as temporary:
            path = Path(temporary.name)
            try:
                temporary.write("\n".join(lines) + "\n")
                temporary.flush()
                os.fsync(temporary.fileno())
                os.replace(path, CONFIG)
            finally:
                path.unlink(missing_ok=True)
    finally:
        os.close(directory)


if __name__ == "__main__":
    if sys.argv[1] == "show":
        show()
    elif sys.argv[1] == "apply":
        try:
            save(read_values())
        except ValueError as error:
            sys.exit(str(error))
