from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .events import ANALYST_GROUP, customer_group


class _Base(AsyncJsonWebsocketConsumer):
    group = None

    async def get_group(self, user):  # return a group name, or None to refuse
        raise NotImplementedError

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close(code=4401)
            return
        self.group = await self.get_group(user)
        if not self.group:
            await self.close(code=4403)
            return
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if self.group:
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        pass  # server-push only

    async def push(self, event):
        await self.send_json(event["data"])


class CustomerConsumer(_Base):
    async def get_group(self, user):
        return customer_group(user.pk)


class AnalystConsumer(_Base):
    async def get_group(self, user):
        allowed = await database_sync_to_async(user.has_perm)("wallets.review_transaction")
        return ANALYST_GROUP if allowed else None