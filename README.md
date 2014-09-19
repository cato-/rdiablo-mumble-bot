txmumble
========

Library for python twisted for communicating with mumble. Taken from 
https://github.com/Chaosteil/rdiablo-mumble-bot

Before running, make sure that google protobuf is installed as well as twisted.

You can get the Mumble.proto file from
https://github.com/mumble-voip/mumble/blob/master/src/Mumble.proto

Compile the proto file with the following parameters:
    protoc --python_out=. Mumble.proto

This will generate a `Mumble_pb2.py` file in this directory.
