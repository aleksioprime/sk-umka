"""Принимать сообщения устройств UMKA из MQTT и сохранять в PostgreSQL.

Топики: umka/<device_id>/status - состояние устройства (раз в минуту, retain);
        umka/<device_id>/event  - событие сортировки или ответа (QoS 1).
Формат сообщений - web/README.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from aiomqtt import Client, MqttError
from sqlalchemy.dialects.postgresql import insert

from shared.db import Device, Event, MqttMessage, Session, create_tables

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("worker")


def parse_time(value) -> datetime:
    """ISO-время устройства; без него - время приёма."""
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


async def apply_message(session, device_id: str | None, kind: str, raw: bytes, now: datetime) -> str:
    if not device_id or kind not in {"status", "event"}:
        return "unsupported"
    try:
        payload = json.loads(raw.decode("utf-8"))
        # PostgreSQL и браузер должны принять JSON с такими же значениями.
        json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, RecursionError):
        return "invalid"
    if not isinstance(payload, dict):
        return "invalid"
    if kind == "event":
        event_id = payload.get("event_id")
        event_kind = payload.get("event")
        if (not isinstance(event_id, str) or not 1 <= len(event_id) <= 64 or "\0" in event_id
                or event_kind not in ("sort", "feedback")):
            return "invalid"
    if len(device_id) > 64:
        return "unregistered"
    device = await session.get(Device, device_id, with_for_update=True)
    if device is None or device.deleted_at is not None:
        return "unregistered"
    device.last_seen = now
    if kind == "status":
        device.status = payload
        return "accepted"

    saved = await session.scalar(insert(Event).values(
        device_id=device_id,
        event_id=event_id,
        kind=event_kind,
        timestamp=parse_time(payload.get("timestamp")),
        payload=payload,
    ).on_conflict_do_nothing().returning(Event.id))
    return "duplicate" if saved is None else "accepted"


async def handle(topic: str, payload: bytes, qos: int, retained: bool) -> None:
    """Записать исходное сообщение и результат приёма в одной транзакции."""
    parts = topic.split("/")
    umka_topic = len(parts) >= 2 and parts[0] == "umka"
    device_id = (parts[1] or None) if umka_topic else None
    kind = parts[2] if umka_topic and len(parts) == 3 else ""
    now = datetime.now(timezone.utc)
    async with Session() as session:
        result = await apply_message(session, device_id, kind, payload, now)
        session.add(MqttMessage(
            received_at=now, topic=topic, device_id=device_id, result=result,
            qos=qos, retained=retained, payload=payload,
        ))
        await session.commit()
    LOG.info("%s %s", topic, result)


async def main() -> None:
    await create_tables()
    topic = os.environ.get("MQTT_TOPIC", "umka/+/+")
    while True:
        try:
            async with Client(
                hostname=os.environ.get("MQTT_HOST", "mosquitto"),
                port=int(os.environ.get("MQTT_PORT", "1883")),
                username=os.environ.get("MQTT_USERNAME") or None,
                password=os.environ.get("MQTT_PASSWORD") or None,
            ) as client:
                await client.subscribe(topic, qos=1)
                LOG.info("Подписка на %s", topic)
                async for message in client.messages:
                    try:
                        await handle(message.topic.value, bytes(message.payload or b""), message.qos, message.retain)
                    except Exception:
                        LOG.exception("Сообщение %s не сохранено", message.topic.value)
        except MqttError as exc:
            LOG.error("MQTT: %s, переподключение через 5 с", exc)
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
