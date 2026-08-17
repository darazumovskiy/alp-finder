#!/usr/bin/env python3
"""Список участников TG-группы + число сообщений каждого -> analysis/tg_participants.json.

Нужен для аналитики активности: отдельно видны «молчуны» — вступили, но не
написали ни одного сообщения. Работает по копии сессии, чтобы не конфликтовать
с крон-докачкой tg_export.py.

Запуск: . scripts/.tg_env && python3 scripts/tg_participants.py
"""

import json
import shutil
import tempfile
from pathlib import Path

from telethon.sync import TelegramClient
from telethon.tl.functions.channels import GetParticipantsRequest
from telethon.tl.types import ChannelParticipantsSearch

import os
import sys

ROOT = Path(__file__).resolve().parent.parent
SESSION_SRC = ROOT / "scripts" / ".tg_session.session"
OUT = ROOT / "analysis" / "tg_participants.json"
INVITE_HASH = "e-P0uc1EehozOTg0"


def main():
    api_id, api_hash = os.environ.get("TG_API_ID"), os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        sys.exit("Нужны TG_API_ID и TG_API_HASH (. scripts/.tg_env)")

    tmp = Path(tempfile.mkdtemp()) / "part.session"
    shutil.copy(SESSION_SRC, tmp)
    client = TelegramClient(str(tmp), int(api_id), api_hash)

    with client:
        # не CheckChatInviteRequest: его дёргает крон и ловится FloodWait;
        # берём группу из кеша сессии по id канала (docs/telegram/README.md)
        from telethon.tl.types import PeerChannel
        group = client.get_entity(PeerChannel(4466042035))

        # участники: raw-запрос ради даты вступления (participant.date)
        members = {}
        offset = 0
        while True:
            res = client(GetParticipantsRequest(
                group, ChannelParticipantsSearch(""), offset, 200, hash=0))
            if not res.participants:
                break
            users = {u.id: u for u in res.users}
            for p in res.participants:
                uid = getattr(p, "user_id", None)
                if uid is None:
                    continue
                u = users.get(uid)
                name = " ".join(filter(None, [
                    getattr(u, "first_name", None), getattr(u, "last_name", None)])) if u else str(uid)
                members[uid] = {
                    "id": uid,
                    "name": name or str(uid),
                    "username": getattr(u, "username", None) if u else None,
                    "joined": p.date.strftime("%Y-%m-%d %H:%M") if getattr(p, "date", None) else None,
                    "messages": 0,
                }
            offset += len(res.participants)
        print(f"участников: {len(members)}")

        # счётчик сообщений по sender_id (без скачивания медиа)
        n = 0
        for msg in client.iter_messages(group):
            n += 1
            sid = msg.sender_id
            if sid in members and msg.action is None:
                members[sid]["messages"] += 1
        print(f"сообщений просмотрено: {n}")

    out = sorted(members.values(), key=lambda m: (-m["messages"], m["name"].lower()))
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    silent = [m for m in out if m["messages"] == 0]
    print(f"молчунов: {len(silent)} -> {OUT}")


if __name__ == "__main__":
    main()
