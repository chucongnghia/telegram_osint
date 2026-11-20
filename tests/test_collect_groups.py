"""Thu thập thêm từ group thảo luận gắn với kênh (--include-linked-groups)."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

HAS_TELETHON = importlib.util.find_spec("telethon") is not None

if HAS_TELETHON:
    from telegram_demo.cli import demo_telegram_api as api
    from telethon.tl.types import Channel, PeerChannel


def make_channel(username="chan", channel_id=100):
    return Channel(id=channel_id, title="Kênh", username=username, photo=None, date=None,
                   broadcast=True, megagroup=False)


def make_group(username="grp", group_id=55):
    return Channel(id=group_id, title="Group thảo luận", username=username, photo=None, date=None,
                   broadcast=False, megagroup=True)


@unittest.skipUnless(HAS_TELETHON, "cần telethon")
class LinkedGroupTests(unittest.IsolatedAsyncioTestCase):

    async def test_linked_group_entity_resolves_peer(self):
        group = make_group()
        seen = []

        class FakeClient:
            async def __call__(self, request):
                self.request = request
                return SimpleNamespace(full_chat=SimpleNamespace(linked_chat_id=group.id))

            async def get_entity(self, peer):
                seen.append(peer)
                return group

        result = await api.linked_group_entity(FakeClient(), make_channel())
        self.assertIs(result, group)
        self.assertIsInstance(seen[0], PeerChannel)
        self.assertEqual(seen[0].channel_id, group.id)

    async def test_linked_group_entity_none_without_link(self):
        class FakeClient:
            async def __call__(self, request):
                return SimpleNamespace(full_chat=SimpleNamespace(linked_chat_id=None))

            async def get_entity(self, peer):  # pragma: no cover - không được gọi
                raise AssertionError("không nên mở group khi kênh không có link")

        self.assertIsNone(await api.linked_group_entity(FakeClient(), make_channel()))

    async def test_linked_group_entity_ignores_non_channel(self):
        self.assertIsNone(await api.linked_group_entity(SimpleNamespace(), SimpleNamespace(id=1)))

    async def test_fetch_linked_group_tags_records(self):
        group = make_group(username="discuss", group_id=77)
        original_group, original_fetch = api.linked_group_entity, api.fetch_history
        api.linked_group_entity = lambda client, entity: _async(group)

        async def fake_fetch(client, target, limit, **kwargs):
            self.assertEqual(target, group)
            return [{"message_id": 5, "url": "x", "channel": "cũ"}]

        api.fetch_history = fake_fetch

        class FakeClient:
            async def get_entity(self, target):
                return make_channel()

        try:
            records = await api.fetch_linked_group(FakeClient(), "@chan", 3, "@chan")
        finally:
            api.linked_group_entity, api.fetch_history = original_group, original_fetch
        self.assertEqual(records[0]["channel"], "discuss")
        self.assertEqual(records[0]["linked_group_of"], "@chan")
        self.assertEqual(records[0]["url"], "https://t.me/discuss/5")

    async def test_numeric_target_is_passed_to_telethon_as_integer(self):
        seen = []

        class FakeClient:
            async def get_entity(self, target):
                seen.append(target)
                return make_channel()

            def iter_messages(self, entity, limit=None):
                async def gen():
                    yield SimpleNamespace(id=1, action=None)
                return gen()

        original_record = api.to_record
        api.to_record = lambda msg, entity: {"message_id": msg.id, "posted_at": "2026-01-01"}
        try:
            records = await api.fetch_history(FakeClient(), "-1001234567890", 1, progress=lambda *_: None)
        finally:
            api.to_record = original_record
        self.assertEqual(seen, [-1001234567890])
        self.assertEqual(len(records), 1)


    async def test_discover_linked_groups_builds_source_rows(self):
        group = make_group(username="discuss", group_id=77)
        original = api.linked_group_entity
        api.linked_group_entity = lambda client, entity: _async(group if entity.username != "nochan" else None)

        class FakeClient:
            async def get_entity(self, target):
                return make_channel(username="nochan" if target == "nochan" else "chan")

        try:
            rows = await api.discover_linked_groups(FakeClient(), ["chan", "chan", "nochan"])
        finally:
            api.linked_group_entity = original
        self.assertEqual(len(rows), 1, "dedupes by username and skips channels without a group")
        row = rows[0]
        self.assertEqual(row["username"], "discuss")
        self.assertEqual(row["kind"], "group")
        self.assertEqual(row["linked_group_of"], "chan")
        self.assertEqual(row["keywords"], ["group của chan"])

    async def test_discover_linked_groups_accepts_numeric_target(self):
        seen = []

        class FakeClient:
            async def get_entity(self, target):
                seen.append(target)
                return make_channel(username="chan")

        original_linked, original_rows = api.linked_group_entity, api.discover_linked_groups
        api.linked_group_entity = lambda client, entity: _async(make_group())
        api.discover_linked_groups = original_rows
        try:
            await api.discover_linked_groups(FakeClient(), ["-1001234567890"])
        finally:
            api.linked_group_entity = original_linked
        self.assertEqual(seen, [-1001234567890])


def _async(value):
    async def run():
        return value
    return run()


if __name__ == "__main__":
    unittest.main()
