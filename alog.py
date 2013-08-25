#!/usr/bin/env python

from auditor import alog

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Log or Retreive and event.")

    parser.add_argument("message", nargs="*", default=None,
                        help="A message to log to Auditor")

    parser.add_argument('--limit', default=15, type=int, help='The amount of records to return.')
    parser.add_argument('--offset', default=0, type=int, help='The offset for records to return.')
    parser.add_argument('--tags', default=[], action="append", help='Which tag to apply to message or filter.')
    parser.add_argument('--user', default="", help='Which tag to apply to message or filter.')
    parser.add_argument('--level', default=None, type=int, help='Which level to apply to message or filter')

    args = parser.parse_args()

    if args.message:
        alog_args = {}

        if args.level is not None: alog_args["level"] = args.level
        if args.user: alog_args["user"] = args.user
        if args.tags:
            new_tags = []
            for tag in args.tags:
                new_tags.extend(tag.split(","))
            alog_args["tags"] = new_tags

        alog(" ".join(args.message), **alog_args)

if __name__ == "__main__":
    main()
