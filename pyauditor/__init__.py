#!/usr/bin/env python

import json
import os
import pytz
import requests
import threading
import time

from datetime import datetime


class Error(Exception):
    pass


class Auditor(object):
    def __init__(self, hostname, port, secure=False, buffer_secs=None):
        self.hostname = hostname
        self.port = port
        self.secure = secure
        self.buffer_secs = buffer_secs
        self.events = Events(self)

    def _request(self, caller, handler, key=None, value=None):
        headers = {'Content-type': 'application/json'}
        kwargs = {
            "headers": headers,
            "timeout": 10,
        }

        if key and value:
            kwargs["data"] = json.dumps({key: value})

        response = caller(
            "http://%s:%s%s" % (self.hostname, self.port, handler), **kwargs)

        data = json.loads(response.text)

        if data["type"] == "error":
            raise Error(data["data"]["msg"])

        return data["data"]

    def _put(self, key, value, handler):
        return self._request(requests.put, key, value, handler)

    def _post(self, key, value, handler):
        return self._request(requests.post, key, value, handler)

    def _get(self, handler):
        return self._request(requests.get, handler)

    def alog(self, summary, tags="", user=None, level=1, close=True):
        data = {
            "summary": summary,
            "user": get_user(user),
            "level": level,
            "start": pytz.UTC.localize(datetime.utcnow()),
        }

        if isinstance(tags, list):
            tags = ", ".join(tags)

        if tags: data["tags"] = tags

        if close:
            data["end"] = data["start"]

        response = json.loads(requests.post("http://%s:%s/event/" % (self.hostname, self.port), data=data).text)

        if response["type"] == "error":
            raise Error(response["data"]["msg"])

        # Don't return an Event at all when doing a simple
        # summary log.
        if close:
            return

        return Event(self, response["data"], self.buffer_secs)


class EventCommiter(threading.Thread):
    def __init__(self, event):
        self.event = event
        super(EventCommiter, self).__init__()

    def run(self):
        last_update = 0
        while not self.event._closing:
            now = time.time()
            if (now - last_update) >= self.event._connection.buffer_secs:
                self.event.commit()
                last_update = time.time()
            time.sleep(.2)


class Events(object):
    def __init__(self, connection):
        self._connection = connection
        self.limit = 50

    def __getitem__(self, val):
        offset = 0
        if not isinstance(val, int):
            if val.start:
                offset = val.start

            limit = self.limit + offset
            if val.stop:
                limit = val.stop
        else:
            limit = val

        events, total = self._get_events(offset, limit)
        return events

    def _get_events(self, offset, limit):
        response = self._connection._get("/event/?offset=%s&limit=%s" % (offset, limit))
        total = response["total"]
        events = [Event(self._connection, event) for event in response["events"]]
        return events, total

    def __iter__(self):
        events, total = self._get_events(0, self.limit)

        for event in events:
            yield event

        # If this is True we need to start paginating.
        if total > len(events):
            for idx in range(1, (total / self.limit) + 1):
                offset = idx * self.limit
                events, _ = self._get_events(offset, offset + self.limit)

                for event in events:
                    yield event



class Event(object):
    def __init__(self, connection, payload):
        self._connection = connection
        self._update(payload)

        self._closing = False
        self._commiter = None

        self._batched_details = {
            "attribute": {},
            "stream": {},
        }
        self._batched_details_lock = threading.RLock()

        self.attrs = DetailsDescriptor(self, "attribute")
        self.streams = DetailsDescriptor(self, "stream")


    def _add_detail(self, details_type, name, value, mode="set"):

        if self._commiter is None:
            self._start_commiter()

        with self._batched_details_lock:
            detail = self._batched_details[details_type]
            if name not in detail:
                detail[name] = {
                    "details_type": details_type,
                    "name": name,
                    "value": [],
                    "mode": "append",
                }

            if mode == "set":
                detail[name]["mode"] = "set"
                detail[name]["value"] = [value]
            elif mode == "append":
                detail[name]["value"].append(value)

        if not self._connection.buffer_secs:
            self.commit()

    def _start_commiter(self):
        if self._connection.buffer_secs:
            # This must be started last so that it has access to all
            # of the attributes when it is started.
            self._commiter = EventCommiter(self)
            self._commiter.daemon = True
            self._commiter.start()

    @staticmethod
    def _build_payload(values):
        payload = []
        for detail in values:
            if detail["details_type"] == "stream":
                payload.append({
                    "details_type": "stream",
                    "name": detail["name"],
                    "value": "".join(detail["value"]),
                    "mode": detail["mode"],
                })
            elif detail["details_type"] == "attribute":
                for idx, val in enumerate(detail["value"]):
                    mode = "append"
                    if detail["mode"] == "set" and idx == 0:
                        mode = "set"
                    payload.append({
                        "details_type": "attribute",
                        "name": detail["name"],
                        "value": val,
                        "mode": mode,
                    })
        return payload

    def commit(self):
        with self._batched_details_lock:
            values = self._batched_details["attribute"].values()
            values += self._batched_details["stream"].values()

            if not len(values):
                return

            self._batched_details["attribute"] = {}
            self._batched_details["stream"] = {}

        self._connection._post("/event/%s/details/" % self.id,
                               "details", self._build_payload(values))

    def _update(self, payload):
        self.id = payload.get("id")
        self.summary = payload.get("summary")
        self.user = payload.get("user")
        self.tags = payload.get("tags", "").split(", ")
        self.start = payload.get("start")
        self.end = payload.get("end")

    def close(self):
        self._closing = True
        self._update(self._connection._put(
            "end", "/event/%s/" % self.id, str(pytz.UTC.localize(datetime.utcnow()))
        ))
        if self._commiter:
            self._commiter.join()
        self.commit()


class DetailsContainer(object):
    """ Wraps a value for a particular detail."""

    def __init__(self, parent, name):
        self.parent = parent
        self.name = name
        self.value = []

    def set(self, elem):
        self.value = [elem]
        self.parent.event._add_detail(
            self.parent.name,
            self.name,
            elem,
            mode="set")

    def append(self, elem):
        self.value.append(elem)
        self.parent.event._add_detail(
            self.parent.name,
            self.name,
            elem,
            mode="append")


class DetailsDescriptor(object):
    """ Acts as a proxy between varios details and their values."""

    def __init__(self, event, name):
        self.event = event
        self.name = name
        self._values = {}

    def __getattr__(self, name):
        if name not in self._values:
            self._values[name] = DetailsContainer(self, name)
        return self._values[name]

    def __getitem__(self, key):
        return self.__getattr__(key)


def get_user(user=None):
    if user is not None:
        return user
    if "SUDO_USER" in os.environ:
        return "%s(%s)" % (os.environ["USER"], os.environ["SUDO_USER"])
    return os.environ["USER"]
