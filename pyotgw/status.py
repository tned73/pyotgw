"""All status related code"""

import asyncio
import logging
from copy import deepcopy
from typing import Awaitable, Callable, Dict, List

from pyotgw import vars as v

_LOGGER = logging.getLogger(__name__)


class StatusManager:
    """Manage status tracking and updates"""

    def __init__(self):
        """Initialise the status manager"""
        self.loop = asyncio.get_event_loop()
        self._updateq = asyncio.Queue()
        self._part_updateq = asyncio.Queue()
        self._status = deepcopy(v.DEFAULT_STATUS)
        self._notify: List[Callable[[Dict], Awaitable[None]]] = []
        self._part_notify: Dict[str, List[Callable[[Dict], Awaitable[None]]]] = dict()
        self._update_task = self.loop.create_task(self._process_updates())
        self._part_update_task = self.loop.create_task(self._process_part_updates())

    def reset(self):
        """Clear the queue and reset the status dict"""
        while not self._updateq.empty():
            self._updateq.get_nowait()
        self._status = deepcopy(v.DEFAULT_STATUS)

    @property
    def status(self):
        """Return the full status dict"""
        return deepcopy(self._status)

    def delete_value(self, part, key):
        """Delete key from status part."""
        try:
            del self._status[part][key]
        except (AttributeError, KeyError):
            return False
        self._updateq.put_nowait(self.status)
        return True

    def submit_partial_update(self, part, update):
        """
        Submit an update for part of the status dict to the queue.
        Return a boolean indicating success.
        """
        if part not in self.status:
            _LOGGER.error("Invalid status part for update: %s", part)
            return False
        if not isinstance(update, dict):
            _LOGGER.error("Update for %s is not a dict: %s", part, update)
            return False
        self._status[part].update(update)
        self._part_updateq.put_nowait({part: update})
        self._updateq.put_nowait(self.status)
        return True

    def submit_full_update(self, update):
        """
        Submit an update for multiple parts of the status dict to the
        queue. Return a boolean indicating success.
        """
        for part, values in update.items():
            # First we verify all data
            if part not in self.status:
                _LOGGER.error("Invalid status part for update: %s", part)
                return False
            if not isinstance(values, dict):
                _LOGGER.error("Update for %s is not a dict: %s", part, values)
                return False
        for part, values in update.items():
            # Then we actually update
            self._status[part].update(values)
        self._updateq.put_nowait(self.status)
        return True

    def subscribe(
        self, callback: Callable[[Dict], Awaitable[None]], part: str = None
    ) -> bool:
        """
        Subscribe callback for future status updates.
        Return boolean indicating success.
        """
        if part:
            if part in self._part_notify:
                if callback in self._part_notify[part]:
                    return False
                self._part_notify[part].append(callback)
                return True
            self._part_notify[part] = [callback]
            return True
        if callback in self._notify:
            return False
        self._notify.append(callback)
        return True

    def unsubscribe(
        self, callback: Callable[[Dict], Awaitable[None]], part: str = None
    ) -> bool:
        """
        Unsubscribe callback from future status updates.
        Return boolean indicating success.
        """
        if part:
            if part in self._part_notify:
                if callback in self._part_notify[part]:
                    self._part_notify[part].pop(callback)
                    if not self._part_notify[part]:
                        self._part_notify.pop(part)
                    return True
            return False
        if callback not in self._notify:
            return False
        self._notify.remove(callback)
        return True

    async def cleanup(self):
        """Clean up task"""
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                self._update_task = None
        if self._part_update_task:
            self._part_update_task.cancel()
            try:
                await self._part_update_task
            except asyncio.CancelledError:
                self._part_update_task = None

    async def _process_updates(self):
        """Process updates from the queue."""
        _LOGGER.debug("Starting reporting routine")
        while True:
            oldstatus = deepcopy(self.status)
            stat = await self._updateq.get()
            if oldstatus != stat and self._notify:
                for coro in self._notify:
                    # Each client gets its own copy of the dict.
                    self.loop.create_task(coro(deepcopy(stat)))

    async def _process_part_updates(self):
        """Process updates from the queue."""
        _LOGGER.debug("Starting part reporting routine")
        while True:
            stat: Dict[str:Dict] = await self._part_updateq.get()
            part, update = list(stat.items())[0]
            for key in self._part_notify:
                listener = key.split(".")
                if part == listener[0] and (
                    len(listener) == 1 or listener[1] in update
                ):
                    # Each client gets its own copy of the dict.
                    for coro in self._part_notify[key]:
                        self.loop.create_task(coro(deepcopy(stat)))
