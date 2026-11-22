"""API мониторинга сети UMKA: устройства, их состояние и события."""

from __future__ import annotations

from base64 import b64encode
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, StringConstraints
from sqlalchemy import and_, func, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from auth import current_admin, current_user, router as auth_router
from shared.db import Device, Event, MqttMessage, Session, create_tables


@asynccontextmanager
async def lifespan(_app):
    # Таблицы пользователей нужны API сразу, даже если worker ещё не запускался.
    await create_tables()
    yield


app = FastAPI(title="UMKA monitoring", lifespan=lifespan)
app.include_router(auth_router)
# Данные устройств - только для вошедших пользователей; /api/health открыт для Coolify.
protected = [Depends(current_user)]
admin_only = [Depends(current_admin)]
ONLINE_WINDOW = timedelta(minutes=3)  # Статус приходит раз в минуту.


class DeviceName(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


class DeviceRegistration(DeviceName):
    id: Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


def device_view(device: Device) -> dict:
    online = bool(device.last_seen and datetime.now(timezone.utc) - device.last_seen < ONLINE_WINDOW)
    return {"id": device.id, "name": device.name, "online": online,
            "lastSeen": device.last_seen, "status": device.status}


async def active_device(session, device_id: str) -> Device:
    item = await session.get(Device, device_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, "Устройство не найдено")
    return item


@app.get("/api/health")
async def health() -> dict:
    """Проверка для Docker и Coolify: API отвечает и база доступна."""
    async with Session() as session:
        await session.execute(select(1))
    return {"ok": True}


@app.get("/api/devices", dependencies=protected)
async def devices() -> list[dict]:
    async with Session() as session:
        result = await session.scalars(select(Device).where(Device.deleted_at.is_(None)).order_by(Device.id))
        return [device_view(device) for device in result]


@app.post("/api/devices", status_code=201, dependencies=admin_only)
async def register_device(body: DeviceRegistration) -> dict:
    query = insert(Device).values(id=body.id, name=body.name)
    query = query.on_conflict_do_update(
        index_elements=[Device.id],
        set_=dict(name=body.name, deleted_at=None, last_seen=None, status=None),
        where=Device.deleted_at.is_not(None),
    ).returning(Device)
    async with Session() as session:
        item = await session.scalar(query)
        if item is None:
            raise HTTPException(409, "Устройство с таким идентификатором уже зарегистрировано")
        await session.commit()
        return device_view(item)


@app.get("/api/devices/{device_id}", dependencies=protected)
async def device(device_id: str) -> dict:
    async with Session() as session:
        return device_view(await active_device(session, device_id))


@app.patch("/api/devices/{device_id}", dependencies=admin_only)
async def rename_device(device_id: str, body: DeviceName) -> dict:
    async with Session() as session:
        item = await session.scalar(update(Device).where(
            Device.id == device_id, Device.deleted_at.is_(None),
        ).values(name=body.name).returning(Device))
        if item is None:
            raise HTTPException(404, "Устройство не найдено")
        await session.commit()
        return device_view(item)


@app.delete("/api/devices/{device_id}", status_code=204, dependencies=admin_only)
async def remove_device(device_id: str) -> Response:
    async with Session() as session:
        removed = await session.scalar(update(Device).where(
            Device.id == device_id, Device.deleted_at.is_(None),
        ).values(deleted_at=func.now()).returning(Device.id))
        if removed is None:
            raise HTTPException(404, "Устройство не найдено")
        await session.commit()
    return Response(status_code=204)


@app.get("/api/devices/{device_id}/events", dependencies=protected)
async def events(device_id: str, limit: int = 100) -> list[dict]:
    async with Session() as session:
        await active_device(session, device_id)
        result = await session.scalars(
            select(Event).where(Event.device_id == device_id)
            .order_by(Event.timestamp.desc()).limit(min(limit, 1000)))
        return [{"eventId": e.event_id, "kind": e.kind, "timestamp": e.timestamp, **e.payload} for e in result]


@app.get("/api/summary", dependencies=protected)
async def summary() -> dict:
    """Сводка по сети: устройства и сортировки по типам отходов."""
    async with Session() as session:
        rows = await session.execute(
            # Группировка по номеру столбца: ключ JSON в SELECT и GROUP BY иначе
            # передаётся разными параметрами, и PostgreSQL не считает их одним выражением.
            select(Event.payload["waste_type"].as_string(), func.count())
            .join(Device, Event.device_id == Device.id)
            .where(Event.kind == "sort", Device.deleted_at.is_(None)).group_by(text("1")))
        devices_total = await session.scalar(select(func.count()).select_from(Device).where(Device.deleted_at.is_(None)))
        return {"devices": devices_total, "sortedByType": {kind or "unknown": count for kind, count in rows}}


@app.get("/api/devices/{device_id}/sorts", dependencies=protected)
async def device_sorts(
    device_id: str,
    days: int = Query(0, ge=0, le=365),
    waste_type: str | None = None,
    answer: Literal["yes", "no", "none"] | None = None,
    before: int | None = Query(None, gt=0),
    limit: int = Query(25, ge=1, le=100),
) -> dict:
    """Журнал завершённых сортировок с ответами и фильтрами по устройству."""
    feedback = aliased(Event)
    response = feedback.payload["answer"].as_string()
    query = (select(Event, response)
             .outerjoin(feedback, and_(feedback.device_id == Event.device_id,
                                       feedback.event_id == Event.event_id,
                                       feedback.kind == "feedback"))
             .where(Event.device_id == device_id, Event.kind == "sort"))
    if days:
        query = query.where(Event.timestamp >= datetime.now(timezone.utc) - timedelta(days=days))
    if waste_type:
        query = query.where(Event.payload["waste_type"].as_string() == waste_type)
    if answer:
        query = query.where(response.is_(None) if answer == "none" else response == answer)

    async with Session() as session:
        await active_device(session, device_id)
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        if before:
            cursor = await session.scalar(select(Event).where(
                Event.id == before, Event.device_id == device_id, Event.kind == "sort"))
            if cursor is None:
                raise HTTPException(400, "Страница журнала недоступна")
            query = query.where(tuple_(Event.timestamp, Event.id) < tuple_(cursor.timestamp, cursor.id))
        rows = (await session.execute(
            query.order_by(Event.timestamp.desc(), Event.id.desc()).limit(limit + 1)
        )).all()
    return {
        "total": total,
        "next": rows[limit - 1][0].id if len(rows) > limit else None,
        "items": [
            {**event.payload, "timestamp": event.timestamp, "answer": reply}
            for event, reply in rows[:limit]
        ],
    }


@app.get("/api/mqtt/messages", dependencies=admin_only)
async def mqtt_messages(
    hours: int = Query(24, ge=0, le=24 * 365),
    topic: str = Query("", max_length=256),
    device_id: str = Query("", max_length=256),
    result: Literal["accepted", "unregistered", "invalid", "unsupported", "duplicate"] | None = None,
    before: int | None = Query(None, gt=0),
    limit: int = Query(25, ge=1, le=100),
) -> dict:
    query = select(
        MqttMessage.id, MqttMessage.received_at, MqttMessage.topic, MqttMessage.device_id,
        MqttMessage.result, MqttMessage.qos, MqttMessage.retained,
        func.octet_length(MqttMessage.payload).label("size"),
    )
    if hours:
        query = query.where(MqttMessage.received_at >= datetime.now(timezone.utc) - timedelta(hours=hours))
    if topic:
        query = query.where(MqttMessage.topic.contains(topic, autoescape=True))
    if device_id:
        query = query.where(MqttMessage.device_id == device_id)
    if result:
        query = query.where(MqttMessage.result == result)
    if before:
        query = query.where(MqttMessage.id < before)
    async with Session() as session:
        rows = (await session.execute(query.order_by(MqttMessage.id.desc()).limit(limit + 1))).mappings().all()
    return {
        "next": rows[limit - 1]["id"] if len(rows) > limit else None,
        "items": [dict(row) for row in rows[:limit]],
    }


@app.get("/api/mqtt/messages/{message_id}", dependencies=admin_only)
async def mqtt_payload(message_id: int) -> dict:
    async with Session() as session:
        payload = await session.scalar(select(MqttMessage.payload).where(MqttMessage.id == message_id))
    if payload is None:
        raise HTTPException(404, "Сообщение не найдено")
    try:
        return {"encoding": "utf-8", "payload": payload.decode("utf-8")}
    except UnicodeDecodeError:
        return {"encoding": "base64", "payload": b64encode(payload).decode("ascii")}
