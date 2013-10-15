#!/usr/bin/env python

import pika
import json
import os
import pytz
import requests

from datetime import datetime


class Event(object):
    def __init__(self, payload):
        self._update(payload)

    def _put(self, key, value, handler):
        headers = {'Content-type': 'application/json'}
        response = requests.put(
            "http://localhost:8000%s" % (handler,),
            data=json.dumps({key: value}),
            headers=headers
        )
        data = json.loads(response.text)
        if data["type"] == "error":
            raise Error(data["data"]["msg"])
        return data["data"]

    def _post(self, key, value, handler):
        headers = {'Content-type': 'application/json'}
        response = requests.post(
            "http://localhost:8000%s" % (handler,),
            data=json.dumps({key: value}),
            headers=headers
        )
        data = json.loads(response.text)
        if data["type"] == "error":
            raise Error(data["data"]["msg"])
        return data["data"]

    def set_key_value(self, key, value):
        """Sets a dynamic key/value. Fails if used on key with multiple values."""
        self._post("attribute", {str(key): str(value)}, "/event/%s/details/" % self.id)

    def add_key_value(self, key, value):
        """Used to append values to a key. These values are considered immutable."""
        self._put("attribute", {str(key): str(value)}, "/event/%s/details/" % self.id)

    def create_stream(self, name, text):
        """Create a new named stream."""
        self._post("stream", {"name": name, "text": text}, "/event/%s/details/" % self.id)

    def append_stream(self, name, text):
        """Used to append to a named stream."""
        self._put("stream", {"name": name, "text": text}, "/event/%s/details/" % self.id)

    def _update(self, payload):
        self.id = payload.get("id")
        self.summary = payload.get("summary")
        self.user = payload.get("user")
        self.tags = payload.get("tags", "").split(", ")
        self.start = payload.get("start")
        self.end = payload.get("end")

    def close(self):
        self._update(self._put("end", str(pytz.UTC.localize(datetime.utcnow())), "/event/%s/" % self.id))


class Error(Exception):
    pass


def get_user(user=None):
    if user is not None:
        return user
    if "SUDO_USER" in os.environ:
        return "%s(%s)" % (os.environ["USER"], os.environ["SUDO_USER"])
    return os.environ["USER"]



def alog(summary, tags="", user=None, level=1, end_now=True):
    data = {
        "summary": summary,
        "user": get_user(user),
        "level": level,
        "start": pytz.UTC.localize(datetime.utcnow()),
    }

    if isinstance(tags, list):
        tags = ", ".join(tags)

    if tags: data["tags"] = tags

    if end_now:
        data["end"] = data["start"]

    response = json.loads(requests.post("http://localhost:8000/event/", data=data).text)

    if response["type"] == "error":
        raise Error(response["data"]["msg"])

    return Event(response["data"])


def subscribe(headers, callback):
    connection = pika.BlockingConnection(pika.ConnectionParameters(host='localhost'))
    channel = connection.channel()

    result = channel.queue_declare(exclusive=True)
    if not result:
        raise Error('Queue didnt declare properly!')
    queue_name = result.method.queue

    def inner_callback(ch, method, properties, body):
        data = json.loads(body)
        callback(data)

    base_headers = {
        "x-match": "all",
    }

    for key, value in headers.iteritems():
        if key == "tags":
            if isinstance(value, basestring):
                value = [value]
            for tag in value:
                base_headers["tag_%s" % tag] = "1"
        else:
            base_headers[key] = value

    channel.queue_bind(exchange='amq.headers',
                       queue = queue_name,
                       routing_key = '',
                       arguments = base_headers)

    channel.basic_consume(inner_callback, queue=queue_name, no_ack=True)
    try:
        channel.start_consuming()
    finally:
        connection.close()



