"""Локальные детекторы и сохранённый выбор устройства."""

from __future__ import annotations

import json
import logging
from dataclasses import replace

from .config import BASE_DIR
from .detector import YoloDetector


LOG = logging.getLogger(__name__)


class ModelMenu:
    def __init__(self, config, dataset):
        self.config = config
        self.dataset = dataset
        self.settings = config.dataset_settings_path.with_name("model-selection.json")
        self.active = None
        self.detector = None
        self.label_ids = {}

    def paths(self):
        paths = {"default": self.config.model_path}
        for group, directory in (("bundled", BASE_DIR / "models"),
                                 ("device", self.settings.parent / "models")):
            for path in sorted(directory.glob("*.pt")):
                if path.is_file():
                    paths[f"{group}/{path.name}"] = path
        return paths

    def snapshot(self):
        return {"active": self.active, "models": [
            {"id": key, "name": path.name} for key, path in self.paths().items()
        ]}

    def start(self, restore=True):
        saved = {}
        if self.settings.is_file():
            try:
                saved = json.loads(self.settings.read_text(encoding="utf-8"))
                if not isinstance(saved, dict) or not isinstance(saved.get("classes"), list):
                    raise ValueError("Ожидается список классов")
                self.dataset.class_names = list(saved["classes"])
            except (OSError, ValueError) as exc:
                LOG.warning("Не удалось прочитать выбор модели: %s", exc)
                saved = {}
        selected = saved.get("active", "default") if restore else "default"
        try:
            self.select(selected)
        except Exception:
            if selected == "default":
                raise
            LOG.exception("Сохранённая модель недоступна, загружаю основную")
            self.select("default")

    def select(self, key):
        path = self.paths().get(key)
        if path is None:
            raise ValueError("Модель отсутствует в списке устройства")
        if key == self.active:
            return
        detector = YoloDetector(replace(self.config, model_path=path))
        if detector.model.task != "detect":
            raise ValueError("Требуется модель YOLO для обнаружения объектов")
        # Ошибки вычислений обнаруживаются до замены работающей модели.
        import numpy as np
        detector.detect(np.zeros((self.config.image_size, self.config.image_size, 3), dtype=np.uint8))
        names = detector.model.names
        items = names.items() if isinstance(names, dict) else enumerate(names)
        classes = list(self.dataset.class_names)
        label_ids = {}
        for index, name in sorted(items):
            if name not in classes:
                classes.append(name)
            label_ids[index] = classes.index(name)
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        temp = self.settings.with_suffix(".tmp")
        temp.write_text(json.dumps({"active": key, "classes": classes}, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.settings)
        self.dataset.class_names = classes
        self.label_ids = label_ids
        self.active = key
        self.detector = detector
