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

import json
import logging
import optparse

from twisted.internet import reactor
from twisted.internet.protocol import ClientFactory
from twisted.names import client as DNSClient

from txmumble.protocol import MumbleProtocol, mumble_connect

import txmumble.Mumble_pb2
import socket


log = logging.getLogger(__name__)


class DiabloMumbleBot(MumbleProtocol):
    def __init__(self, config):
        MumbleProtocol.__init__(self,
                                username=config['username'],
                                password=config['password'],
                                tokens=config['tokens'])

        self.config = config
        self.dns = DNSClient.getResolver()

        # Session is to identify us from other users
        self.session = None
        # Channel is to save the requested channel ID
        self.channel = None
        # Saves the sessions of the users
        self.users = {}

    def check_address(self, addresses, user=None):
        reason = addresses[0][0].payload
        log.info("Received address: %s" % str(reason))

        if self.config['response_kick']:
            self.user_dirty(user)
        else:
            self.user_clean(user)

    def check_address_failure(self, reason, user=None):
        if self.config['response_kick']:
            self.user_clean(user)
        else:
            self.user_dirty(user)

    def user_dirty(self, user):
        log.info("User %s is dirty! Kicking..." % self.users[user])

        if self.config['kick_diagnostic']:
            log.info("Kick Diagnostic is turned on. No actual kick.")
            return

        remove = Mumble_pb2.UserRemove()
        remove.session = user
        remove.reason = self.config['kick_reason']
        self.sendProtobuf(remove)

    def user_clean(self, user):
        log.info("User %s is clean." % self.users[user])

    def connectionLost(self, reason):
        reactor.stop()

    def recvProtobuf(self, msg_type, message):
        msg_class = message.__class__

        if msg_class == Mumble_pb2.PermissionDenied:
            # Print out if permission was denied for something
            log.warning("Permission denied:\n%s" % str(message))

        elif msg_class == Mumble_pb2.ChannelState:
            # We assign the channel we want first
            if message.name == self.config['channel']:
                self.channel = message.channel_id

        elif msg_class == Mumble_pb2.UserState:
            # Assign our session when we retrieve it
            if message.name:
                self.users[message.session] = message.name

            if message.name == self.username:
                self.session = message.session

                if self.channel and message.channel_id != self.channel:
                    log.info("Joining channel '%s'" % self.config['channel'])
                    state = Mumble_pb2.UserState()
                    state.session = self.session
                    state.channel_id = self.channel
                    self.sendProtobuf(state)

                return

            # Request user stats for the user
            stats = Mumble_pb2.UserStats()
            stats.session = message.session
            self.sendProtobuf(stats)

        elif msg_class == Mumble_pb2.UserStats:
            # We ignore our session
            if message.session == self.session:
                return

            # We act only if the address is exposed to us, for obvious reasons
            if message.address:
                adr = message.address

                if len(adr) == 16:
                    # We only want the IPv4 part
                    adr = adr[-4:]

                # Reverse octets
                adr = adr[::-1]

                # Convert to string
                address = socket.inet_ntop(socket.AF_INET, adr)
                dns_lookup = '%s.%s' % (address, self.config['blacklist_dns'])
                log.info("Checking address for User %s with '%s'"
                         % (self.users[message.session], dns_lookup))

                d = self.dns.lookupAddress(dns_lookup,
                                           timeout=[1, 2, 5, 10])

                # Add our handling of the retrieved user
                d.addCallback(self.check_address, user=message.session)
                d.addErrback(self.check_address_failure, user=message.session)
            else:
                log.warning("IP Address not exposed. Not enough rights?")


class DiabloMumbleBotFactory(ClientFactory):
    def __init__(self, config):
        self.config = config

    def buildProtocol(self, addr):
        return DiabloMumbleBot(self.config)


def main():
    optp = optparse.OptionParser(description="r/Diablo Mumble Bot",
                                 prog="rdiablo_mumble_bot.py",
                                 version="%prog 1.0",
                                 usage="%prog -c config.json")
    optp.add_option("-c", "--config", help="JSON Configuration file",
                    action="store", type="string", default="config.json")
    optp.add_option("-d", "--debug", help="Enable debug output",
                    action="store_true")

    opt, args = optp.parse_args()

    # Check logging type
    if opt.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    # Read JSON config. See sample_config.json for samples.
    with open(opt.config) as config:
        cfg = json.loads(config.read())

    factory = DiabloMumbleBotFactory(cfg)

    # Use our handy helper function to connect to the mumble server via SSL
    mumble_connect(cfg['host'], cfg['port'], factory)

    reactor.run()

if __name__ == '__main__':
    main()
