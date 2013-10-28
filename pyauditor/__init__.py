#!/usr/bin/env python

import pika
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
        self.buffer_secs = buffer_secs


    def _put(self, key, value, handler):
        headers = {'Content-type': 'application/json'}
        response = requests.put(
            "http://%s:%s%s" % (self.hostname, self.port, handler,),
            data=json.dumps({key: value}),
            headers=headers, timeout=5
        )
        data = json.loads(response.text)
        if data["type"] == "error":
            raise Error(data["data"]["msg"])
        return data["data"]

    def _post(self, key, value, handler):
        headers = {'Content-type': 'application/json'}
        response = requests.post(
            "http://%s:%s%s" % (self.hostname, self.port, handler,),
            data=json.dumps({key: value}),
            headers=headers, timeout=5
        )
        data = json.loads(response.text)
        if data["type"] == "error":
            raise Error(data["data"]["msg"])
        return data["data"]

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
        while not self.event.closing:
            now = time.time()
            print "Running"
            if (now - last_update) >= self.event.buffer_secs:
                self.event.commit()
                last_update = time.time()
            time.sleep(.1)
        print "Fell off"


class Event(object):
    def __init__(self, connection, payload, buffer_secs=None):
        self.connection = connection
        self._update(payload)

        self.buffer_secs = buffer_secs
        self.closing = False
        self.commiter = None

        self._batched_details = []
        self._batched_details_lock = threading.RLock()

        self.attrs = DetailsDescriptor(self, "attribute")
        self.streams = DetailsDescriptor(self, "stream")

        if buffer_secs:
            # This must be started last so that it has access to all
            # of the attributes when it is started.
            self.commiter = EventCommiter(self)
            self.commiter.daemon = True
            self.commiter.start()


    def _add_detail(self, details_type, name, value, mode="set"):
        with self._batched_details_lock:
            self._batched_details.append({
                "details_type": details_type,
                "name": name,
                "value": value,
                "mode": mode
            })
        if not self.buffer_secs:
            self.commit()

    def commit(self):
        print "Commiting!", len(self._batched_details)
        with self._batched_details_lock:
            if not len(self._batched_details):
                print "Nothing!"
                return
            details = self._batched_details[:]
            self._batched_details = []
        print "Posting!"
        self.connection._post("details", details,
                              "/event/%s/details/" % self.id)
        print "Done Posting!"

    def _update(self, payload):
        self.id = payload.get("id")
        self.summary = payload.get("summary")
        self.user = payload.get("user")
        self.tags = payload.get("tags", "").split(", ")
        self.start = payload.get("start")
        self.end = payload.get("end")

    def close(self):
        print "Closing!"
        self.closing = True
        self._update(self.connection._put("end", str(pytz.UTC.localize(datetime.utcnow())), "/event/%s/" % self.id))
        if self.commiter:
            print "Waiting on Thread!"
            self.commiter.join()
            print "Thread Dead!"
        print "Final Commit"
        self.commit()
        print "Yay!"


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
