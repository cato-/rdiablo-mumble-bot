#!/usr/bin/env python
# -*- coding: utf-8 -*-

###
# Chaosteil 2013
#
# This work has been released into the public domain by the authors. This
# applies worldwide.
#
# If this is not legally possible, the authors grant any entity the right to
# use this work for any purpose, without any conditions, except those conditions
# required by law.
###

import logging
import optparse
import platform
import struct

import Mumble_pb2
from twisted.internet import reactor, protocol, ssl
from twisted.internet.protocol import ClientFactory


log = logging.getLogger(__name__)


class MumbleProtocol(protocol.Protocol):
    VERSION_MAJOR = 1
    VERSION_MINOR = 2
    VERSION_PATCH = 4

    VERSION_DATA = (VERSION_MAJOR << 16) \
                    | (VERSION_MINOR << 8) \
                    | (VERSION_PATCH)

    # From the Mumble protocol documentation
    PREFIX_FORMAT = ">HI"
    PREFIX_LENGTH = 6

    # This specific order of IDs is extracted from
    # https://github.com/mumble-voip/mumble/blob/master/src/Message.h
    ID_MESSAGE = [
        Mumble_pb2.Version,
        Mumble_pb2.UDPTunnel,
        Mumble_pb2.Authenticate,
        Mumble_pb2.Ping,
        Mumble_pb2.Reject,
        Mumble_pb2.ServerSync,
        Mumble_pb2.ChannelRemove,
        Mumble_pb2.ChannelState,
        Mumble_pb2.UserRemove,
        Mumble_pb2.UserState,
        Mumble_pb2.BanList,
        Mumble_pb2.TextMessage,
        Mumble_pb2.PermissionDenied,
        Mumble_pb2.ACL,
        Mumble_pb2.QueryUsers,
        Mumble_pb2.CryptSetup,
        Mumble_pb2.ContextActionModify,
        Mumble_pb2.ContextAction,
        Mumble_pb2.UserList,
        Mumble_pb2.VoiceTarget,
        Mumble_pb2.PermissionQuery,
        Mumble_pb2.CodecVersion,
        Mumble_pb2.UserStats,
        Mumble_pb2.RequestBlob,
        Mumble_pb2.ServerConfig
    ]

    # Reversing the IDs, so we are able to backreference.
    MESSAGE_ID = dict([(v, k) for k, v in enumerate(ID_MESSAGE)])

    PING_REPEAT_TIME = 5

    def __init__(self, username="MumbleTwistedBot", password=None, tokens=[]):
        self.received = ""

        self.username = username
        self.password = password
        self.tokens = []
        self.channels = {}

    def recvProtobuf(self, msg_type, message):
        log.debug("Received message '%s' (%d):\n%s" \
                  % (message.__class__, msg_type, str(message)))

    def connectionMade(self):
        log.debug("Connected to server.")

        # In the mumble protocol you must first send your current message
        # and immediately after that the authentication data.
        #
        # The mumble server will respond with a version message right after
        # this one.
        version = Mumble_pb2.Version()

        version.version = MumbleProtocol.VERSION_DATA
        version.release = "%d.%d.%d" % (MumbleProtocol.VERSION_MAJOR,
                                       MumbleProtocol.VERSION_MINOR,
                                       MumbleProtocol.VERSION_PATCH)
        version.os = platform.system()
        version.os_version = "Mumble 1.2.4 Twisted Protocol"

        # Here we authenticate
        auth = Mumble_pb2.Authenticate()
        auth.username = self.username
        if self.password:
            auth.password = self.password
        for token in self.tokens:
            auth.tokens.append(token)
        auth.opus = True

        # And now we send both packets one after another
        self.sendProtobuf(version)
        self.sendProtobuf(auth)

        # Then we initialize our ping handler
        self.init_ping()

    def init_ping(self):
        # Call ping every PING_REPEAT_TIME seconds.
        self.ping = reactor.callLater(MumbleProtocol.PING_REPEAT_TIME, self.ping_handler)

    def ping_handler(self):
        log.debug("Sending ping")

        # Ping has only optional data, no required
        ping = Mumble_pb2.Ping()
        self.sendProtobuf(ping)

        self.init_ping()

    def dataReceived(self, recv):
        # Append our received data
        self.received = self.received + recv

        # If we have enough bytes to read the header, we do that
        while len(self.received) >= MumbleProtocol.PREFIX_LENGTH:
            msg_type, length = \
                    struct.unpack(MumbleProtocol.PREFIX_FORMAT,
                                  self.received[:MumbleProtocol.PREFIX_LENGTH])

            full_length = MumbleProtocol.PREFIX_LENGTH + length

            log.debug("Length: %d" % length)
            log.debug("Message type: %d" % msg_type)

            # Check if this this a valid message ID
            if msg_type not in MumbleProtocol.MESSAGE_ID.values():
                log.error('Message ID not available.')
                self.transport.loseConnection()
                return

            # We need to check if we have enough bytes to fully read the
            # message
            if len(self.received) < full_length:
                log.debug("Need to fill data")
                return

            # Read the specific message
            msg = MumbleProtocol.ID_MESSAGE[msg_type]()
            callback_name = "msg_%s" % msg.__class__.__name__
            # Parse all messages except UDP packages packed in TCP
            if msg_type != 1:
                msg.ParseFromString(
                    self.received[MumbleProtocol.PREFIX_LENGTH:
                                  MumbleProtocol.PREFIX_LENGTH + length])
            else:
                msg = self.received[MumbleProtocol.PREFIX_LENGTH:
                                    MumbleProtocol.PREFIX_LENGTH + length]

            # Handle the message
            log.debug("Message name: %s" % msg.__class__.__name__)
            try:
                if hasattr(self, callback_name):
                    getattr(self, callback_name)(msg_type, msg)
                else:
                    self.recvProtobuf(msg_type, msg)
            except Exception:
                log.error("Exception while handling data.")
                # We abort on exception, because that's the proper thing to do
                self.transport.loseConnection()
                raise

            self.received = self.received[full_length:]

    def msg_Version(self, msg_type, msg):
        version = "%i.%i.%i" % (msg.version >> 16, (msg.version >> 8) & 0xFF, msg.version & 0xFF)
        self.got_version(version, msg.release, msg.os, msg.os_version)
        
    def got_version(self, version, release, os, os_version):
        log.debug("Got Version: %s, %s, %s, %s" % (version, release, os, os_version))

    def msg_ChannelState(self, msg_type, msg):
        channel_name = msg.name
        channel_id = msg.channel_id
        channel_parent = msg.parent
        channel_position = msg.position
        self.got_one_channel(channel_name, channel_id, channel_parent, channel_position)

    def got_one_channel(self, name, id, parent, position):
        self.channels[name] = id

    def msg_ServerSync(self, msg_type, msg):
        self.session = msg.session
        # TODO: decode permission (no doc on this)
        self.got_server_sync(msg.session, msg.max_bandwidth, msg.welcome_text)
        
    def got_server_sync(self, session, max_bandwidth, welcome_text):
        log.info("Server says: %s" % welcome_text)
        print self.channels
        if "Hilfe und Support ~" in self.channels:
            self.join_channel(self.session, self.channels['Hilfe und Support ~'])

    def join_channel(self, session, channel_id):
        state = Mumble_pb2.UserState()
        state.session = session
        state.channel_id = channel_id
        self.sendProtobuf(state)

    def decode_varint(self, m, si=0):
        v = ord(m[si])
        if ((v & 0x80) == 0x00):
            return ((v & 0x7F),1)
        elif ((v & 0xC0) == 0x80):
            return ((v & 0x4F) << 8 | ord(m[si+1]),2)
        elif ((v & 0xF0) == 0xF0):
            if ((v & 0xFC) == 0xF0):
                return (ord(m[si+1]) << 24 | ord(m[si+2]) << 16 | ord(m[si+3]) << 8 | ord(m[si+4]), 5)
            elif ((v & 0xFC) == 0xF4):
                return (ord(m[si+1]) << 56 | ord(m[si+2]) << 48 | ord(m[si+3]) << 40 | ord(m[si+4]) << 32 | ord(m[si+5]) << 24 | ord(m[si+6]) << 16 | ord(m[si+7]) << 8 | ord(m[si+8]),9)
            elif ((v & 0xFC) == 0xF8):
                result, length = self.decode_varint(m, si+1)
                return(-result, length+1)
            elif ((v & 0xFC) == 0xFC):
                return (-(v & 0x03), 1)
            else:
                raise Exception("Error while decoding varint 1")
        elif ((v & 0xF0) == 0xE0):
            return ((v & 0x0F) << 24 | ord(m[si+1]) << 16 | ord(m[si+2]) << 8 | ord(m[si+3]), 4)
        elif ((v & 0xE0) == 0xC0):
            return ((v & 0x1F) << 16 | ord(m[si+1]) << 8 | ord(m[si+2]), 3)
        else:
            raise Exception("Error while decoding varint 2")

    def msg_UDPTunnel(self, msg_type, msg):
        pos = 0

        type_target = ord(msg[pos]) #struct.unpack(">b", msg[pos])[0]
        type_of_packet = type_target >> 5 # filter out type
        target = type_target & 0b00011111 # filter out target
        pos += 1
        
        session, n_session_bytes = self.decode_varint(msg[pos:])
        pos += n_session_bytes
        sequence, n_sequence_bytes = self.decode_varint(msg[pos:])
        pos += n_sequence_bytes

        if type_of_packet == 4: # opus has just one packet per message
            terminator = 1
            length, n_length = self.decode_varint(msg[pos:])
            pos += n_length
        else:
            terminator_length = ord(msg[pos]) #struct.unpack(">b", msg[pos])[0]
            terminator = terminator_length >> 7;
            length = terminator_length & 0b01111111
            pos += 1

        audio_data = msg[pos:pos+length]

        log.debug("UDP type=%i target=%i session=%i sequence=%i terminator=%i length=%i" % (type_of_packet, target, session, sequence, terminator, length))

        self.handle_audio_data(type_of_packet, target, session, sequence, terminator, audio_data)

    def handle_audio_data(self, type_of_pkg, target, session, sequence, terminator, audio_data):
        f=open("out/audio-%i-%06i-%03i.celt" % (type_of_pkg, session, sequence), "a")
        f.write(audio_data)
        f.close()

    def connectionLost(self, reason):
        self.ping.cancel()

    def sendProtobuf(self, message):
        # We find the message ID
        msg_type = MumbleProtocol.MESSAGE_ID[message.__class__]
        # Serialize the message
        msg_data = message.SerializeToString()
        length = len(msg_data)

        # Compile the data with the header
        data = struct.pack(MumbleProtocol.PREFIX_FORMAT, msg_type, length) \
                + msg_data

        # Send the data
        self.transport.write(data)


class MumbleProtocolFactory(ClientFactory):
    def __init__(self, username, password):
        self.username = username
        self.password = password

    def buildProtocol(self, addr):
        return MumbleProtocol(username=self.username,
                              password=self.password)


def mumble_connect(host, port, factory):
    """ Helper function for connecting to a mumble server via SSH. """
    log.info("Connecting to %s:%d via SSL" % (host, port))
    reactor.connectSSL(host, port,
                       factory, ssl.ClientContextFactory())


def main():
    # This helps us run a standalone connection test
    optp = optparse.OptionParser(description="Mumble 1.2.3 protocol",
                                 prog="mumble_protocol.py",
                                 version="%prog 1.0",
                                 usage="%prog -u \"Mumble Bot\" -w \"password\"")
    optp.add_option("-u", "--user", help="Username for the mumble bot",
                    action="store", type="string", default="Mumble Bot")
    optp.add_option("-w", "--password", help="Password for the server",
                    action="store", type="string", default=None)
    optp.add_option("-s", "--server", help="Server to connect to",
                    action="store", type="string", default="localhost")
    optp.add_option("-p", "--port", help="Port to connect to",
                    action="store", type="int", default=64738)
    optp.add_option("-d", "--debug", help="Enable debug output",
                    action="store_true")

    opt, args = optp.parse_args()

    if opt.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    log.info("Mumble username: '%s'" % opt.user)
    factory = MumbleProtocolFactory(username=opt.user, password=opt.password)

    mumble_connect(opt.server, opt.port, factory)
    reactor.run()



if __name__ == '__main__':
    main()
