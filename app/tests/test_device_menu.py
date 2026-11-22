import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from sorter.config import AppConfig
from sorter.dataset import DatasetCollector
from sorter.manual_capture import save_capture
from sorter.model_menu import ModelMenu

try:
    import numpy as np
    import cv2
except ImportError:
    np = None


@unittest.skipIf(np is None, "нужны OpenCV и NumPy")
class DeviceMenuTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.frame = np.zeros((32, 48, 3), dtype=np.uint8)

    def test_usb_unplug_never_falls_back_to_local(self):
        with patch("sorter.manual_capture.usb_volumes", return_value={}), self.assertRaises(RuntimeError):
            save_capture(self.frame, "usb-id", "paper", self.root / "local")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_usb_jpeg_and_mount_identity(self):
        dev = self.root.stat().st_dev
        with patch("sorter.manual_capture.usb_volumes", return_value={
            "volume": (self.root, f"{os.major(dev)}:{os.minor(dev)}"),
            "unmounted": (self.root, "0:0"),
        }):
            with self.assertRaises(RuntimeError):
                save_capture(self.frame, "unmounted", "paper", self.root / "local")
            name = save_capture(self.frame, "volume", "paper", self.root / "local")
        photo = self.root / "umka-dataset" / name
        self.assertEqual(cv2.imread(str(photo)).shape, self.frame.shape)
        self.assertFalse((self.root / "local").exists())

    def test_model_selection_restore_failure_and_stable_label_ids(self):
        config = replace(AppConfig(), dataset_settings_path=self.root / "dataset.json")
        dataset = DatasetCollector(self.root / "dataset", config.dataset_settings_path)
        menu = ModelMenu(config, dataset)
        other = self.root / "models" / "custom.pt"
        other.parent.mkdir()
        other.touch()
        def detector(settings):
            instance = Mock()
            instance.model.task = "detect"
            instance.model.names = {0: "paper", 1: "plastic"} if settings.model_path == config.model_path else {0: "plastic", 1: "glass"}
            return instance
        with patch("sorter.model_menu.YoloDetector", side_effect=detector):
            menu.start()
            menu.select("device/custom.pt")
            self.assertEqual(menu.label_ids, {0: 1, 1: 2})
            restored = ModelMenu(config, dataset)
            restored.start()
            self.assertEqual(restored.active, "device/custom.pt")
            with patch("sorter.model_menu.YoloDetector", side_effect=RuntimeError), self.assertRaises(RuntimeError):
                restored.select("default")
            self.assertEqual(restored.active, "device/custom.pt")
            other.unlink()
            with self.assertLogs("sorter.model_menu", level="ERROR"):
                restored.start()
            self.assertEqual(restored.active, "default")
