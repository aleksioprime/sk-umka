"""Сохранение исходного кадра на плату или на выбранный USB-том."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from contextlib import ExitStack
from pathlib import Path


def usb_volumes() -> dict:
    """Наследовать транспорт диска для его разделов и показывать смонтированные тома."""
    try:
        result = subprocess.run(
            ["lsblk", "--json", "--tree", "-o", "NAME,TRAN,MOUNTPOINT,MAJ:MIN,UUID"],
            capture_output=True, text=True, check=True, timeout=4,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    volumes = {}

    def visit(device, parent_usb=False):
        usb = device.get("tran") == "usb" or parent_usb
        mount = device.get("mountpoint")
        if usb and mount and mount.startswith(("/media/", "/run/media/", "/mnt/")):
            path = Path(mount)
            number = device["maj:min"]
            if os.access(path, os.W_OK):
                key = f"{number}/{device.get('uuid') or device['name']}"
                volumes[key] = (path, number)
        for child in device.get("children", []):
            visit(child, usb)

    for disk in json.loads(result.stdout)["blockdevices"]:
        visit(disk)
    return volumes


def save_capture(frame, destination, category, local_dir):
    import cv2

    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 94])
    if not ok:
        raise RuntimeError("Камера не смогла подготовить снимок")
    filename = f"{category}_{uuid.uuid4().hex}.jpg"
    with ExitStack() as stack:
        def open_dir(path, parent=None):
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, fd)
            return fd

        if destination == "local":
            local_dir.mkdir(parents=True, exist_ok=True)
            folder = open_dir(local_dir)
        else:
            volume = usb_volumes().get(destination)
            if volume is None:
                raise RuntimeError("Выбранная флешка отключена или недоступна для записи")
            mount, number = volume
            root = open_dir(mount)
            device = os.fstat(root).st_dev
            if f"{os.major(device)}:{os.minor(device)}" != number:
                raise RuntimeError("Флешка отключена. Обновите список носителей")
            # Открытый дескриптор удерживает том даже при смене точки монтирования.
            try:
                os.mkdir("umka-dataset", dir_fd=root)
            except FileExistsError:
                pass
            folder = open_dir("umka-dataset", root)
        temp = filename + ".part"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=folder)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded.tobytes())
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(temp, filename, src_dir_fd=folder, dst_dir_fd=folder)
            os.fsync(folder)
        finally:
            try:
                os.unlink(temp, dir_fd=folder)
            except FileNotFoundError:
                pass
    return filename
