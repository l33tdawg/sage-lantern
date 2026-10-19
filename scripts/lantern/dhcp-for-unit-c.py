"""A small DHCP server for the direct Realtek link to Unit C.

This exists because the workstation's Realtek interface is on an isolated point
to point link with no DHCP server on it, so Unit C cannot obtain a lease. macOS
ships `/usr/libexec/bootpd`, but it needs root and a launchd job to configure,
and there is no `dnsmasq` on this box. This is the minimum that answers a
DISCOVER/REQUEST and hands out a bounded lease.

Scope and safety:
  * It answers only on the interface whose address it is told to use, and binds
    that address explicitly, so it cannot serve a network it was not aimed at.
  * The pool is one bounded range. Nothing outside it is ever offered.
  * It offers no default route unless `--router` is given, so enabling it does
    not silently re-route the client's traffic. Pass `--router` only when the
    client should actually use this workstation as a gateway.
  * Addresses already in use are excluded by construction: the caller picks the
    pool, and the defaults deliberately avoid the addresses the Lantern units
    are known to use.

Usage (needs root only to bind UDP/67 and to set the interface address):

    sudo ifconfig en8 inet 192.168.50.1 netmask 255.255.255.0
    sudo python3 scripts/lantern/dhcp-for-unit-c.py --interface en8 \
        --server 192.168.50.1 --pool 192.168.50.100-192.168.50.199

`--port` and `--broadcast-to` exist so the protocol handling can be exercised
against a high port without root, which is how this is tested.
"""
from __future__ import annotations

import argparse
import socket
import struct
import sys
import time

# DHCP message types (RFC 2131 section 9.6).
DISCOVER, OFFER, REQUEST, DECLINE, ACK, NAK = 1, 2, 3, 4, 5, 6

MAGIC = b"\x63\x82\x53\x63"
BROADCAST = "255.255.255.255"

# Options we send.
OPT_SUBNET_MASK = 1
OPT_ROUTER = 3
OPT_DNS = 6
OPT_LEASE_TIME = 51
OPT_MESSAGE_TYPE = 53
OPT_SERVER_ID = 54
OPT_BROADCAST = 28
OPT_END = 255


def parse_pool(spec: str) -> tuple[int, int]:
    """'192.168.50.100-192.168.50.199' -> (first, last) as integers."""
    try:
        first, last = spec.split("-", 1)
    except ValueError:
        raise SystemExit("pool must look like 192.168.50.100-192.168.50.199")
    start, end = ip_to_int(first.strip()), ip_to_int(last.strip())
    if end < start:
        raise SystemExit("pool end is before its start")
    if end - start + 1 > 1024:
        raise SystemExit("refusing a pool larger than 1024 addresses")
    return start, end


def ip_to_int(text: str) -> int:
    try:
        packed = socket.inet_aton(text)
    except OSError:
        raise SystemExit(f"not an IPv4 address: {text}")
    return struct.unpack("!I", packed)[0]


def int_to_ip(value: int) -> str:
    return socket.inet_ntoa(struct.pack("!I", value))


def parse_message(data: bytes) -> dict | None:
    """Return the fields this server needs, or None if it is not a DHCP packet."""
    if len(data) < 240 or data[236:240] != MAGIC:
        return None
    return {
        "op": data[0],
        "xid": data[4:8],
        "chaddr": data[28:34],
        "flags": struct.unpack("!H", data[10:12])[0],
        "options": parse_options(data[240:]),
    }


def parse_options(blob: bytes) -> dict[int, bytes]:
    options, index = {}, 0
    while index < len(blob):
        code = blob[index]
        if code == 0:  # pad
            index += 1
            continue
        if code == OPT_END:
            break
        if index + 1 >= len(blob):
            break
        length = blob[index + 1]
        options[code] = blob[index + 2:index + 2 + length]
        index += 2 + length
    return options


def build_reply(request: dict, *, message_type: int, offered_ip: str, server_ip: str,
                subnet_mask: str, lease_seconds: int, router: str | None,
                dns: list[str]) -> bytes:
    """Build the OFFER or ACK that answers one parsed request."""
    body = bytearray(240)
    body[0] = 2  # BOOTREPLY
    body[1] = 1  # Ethernet
    body[2] = 6  # hardware address length
    body[4:8] = request["xid"]
    # A client that cannot receive unicast before it is configured sets the
    # broadcast flag; honouring it is what makes a bare client work at all.
    body[10:12] = struct.pack("!H", 0x8000)
    body[16:20] = socket.inet_aton(offered_ip)   # yiaddr: the address being handed out
    body[20:24] = socket.inet_aton(server_ip)    # siaddr
    body[28:34] = request["chaddr"]
    body[236:240] = MAGIC

    options = bytearray()
    options += bytes([OPT_MESSAGE_TYPE, 1, message_type])
    options += bytes([OPT_SERVER_ID, 4]) + socket.inet_aton(server_ip)
    options += bytes([OPT_LEASE_TIME, 4]) + struct.pack("!I", lease_seconds)
    options += bytes([OPT_SUBNET_MASK, 4]) + socket.inet_aton(subnet_mask)
    if router:
        options += bytes([OPT_ROUTER, 4]) + socket.inet_aton(router)
    if dns:
        packed = b"".join(socket.inet_aton(entry) for entry in dns)
        options += bytes([OPT_DNS, len(packed)]) + packed
    options += bytes([OPT_BROADCAST, 4]) + socket.inet_aton(BROADCAST)
    options += bytes([OPT_END])
    return bytes(body) + bytes(options)


class Leases:
    """Bounded, deterministic address assignment keyed by client MAC."""

    def __init__(self, first: int, last: int, lease_seconds: int, reserved: set[int]):
        self.first, self.last = first, last
        self.lease_seconds = lease_seconds
        # Never hand out the server's own address or anything else in use.
        self.available = [value for value in range(first, last + 1) if value not in reserved]
        if not self.available:
            raise SystemExit("pool is empty once the reserved addresses are removed")
        self.by_mac: dict[bytes, tuple[int, float]] = {}

    def offer_for(self, mac: bytes, now: float) -> int | None:
        held = self.by_mac.get(mac)
        if held is not None:
            address, _ = held
            return address
        taken = {address for address, _ in self.by_mac.values()}
        for candidate in self.available:
            if candidate not in taken:
                return candidate
        return None

    def commit(self, mac: bytes, address: int, now: float) -> None:
        self.by_mac[mac] = (address, now + self.lease_seconds)

    def expire(self, now: float) -> None:
        for mac, (_, expiry) in list(self.by_mac.items()):
            if expiry <= now:
                del self.by_mac[mac]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interface", required=True)
    parser.add_argument("--server", required=True, help="this interface's own address")
    parser.add_argument("--pool", required=True, help="START-END, inclusive")
    parser.add_argument("--subnet-mask", default="255.255.255.0")
    parser.add_argument("--lease-seconds", type=int, default=3600)
    parser.add_argument("--router", default=None,
                        help="omit to advertise no default route at all")
    parser.add_argument("--dns", default="", help="comma separated; omit for none")
    parser.add_argument("--port", type=int, default=67)
    parser.add_argument("--broadcast-to", default=BROADCAST,
                        help="where replies are sent; a test port can redirect this")
    parser.add_argument("--reply-port", type=int, default=None,
                        help="port replies are sent to; defaults to --port. A real client "
                             "listens on 68, and a test client uses its own ephemeral port")
    parser.add_argument("--listen-address", default=None,
                        help="address to receive on; defaults to --server. Only useful for a "
                             "test, where the server must not hold the address it advertises")
    args = parser.parse_args()
    if args.reply_port is None:
        args.reply_port = args.port
    if args.listen_address is None:
        args.listen_address = args.server

    first, last = parse_pool(args.pool)
    reserved = {ip_to_int(args.server)}
    leases = Leases(first, last, args.lease_seconds, reserved)
    dns = [entry.strip() for entry in args.dns.split(",") if entry.strip()]

    socket_ = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    socket_.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    socket_.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    # Holding UDP/67 is the only part that needs root. In a test the listen
    # address is redirected so the server can be exercised while another process
    # holds the address it advertises.
    socket_.bind((args.listen_address, args.port))

    print(f"dhcp: serving {int_to_ip(first)}-{int_to_ip(last)} on {args.interface} "
          f"as {args.server}, port {args.port}, lease {args.lease_seconds}s", flush=True)
    if not args.router:
        print("dhcp: advertising no default route (client keeps its own)", flush=True)

    while True:
        data, _ = socket_.recvfrom(4096)
        now = time.time()
        leases.expire(now)
        request = parse_message(data)
        if request is None or request["op"] != 1:
            continue
        message_type = request["options"].get(OPT_MESSAGE_TYPE, b"\x00")[0]
        mac = request["chaddr"]
        offered = leases.offer_for(mac, now)
        if offered is None:
            print(f"dhcp: pool exhausted, ignoring {mac.hex()}", flush=True)
            continue
        offered_ip = int_to_ip(offered)

        if message_type == DISCOVER:
            reply_type, label = OFFER, "OFFER"
        elif message_type == REQUEST:
            leases.commit(mac, offered, now)
            reply_type, label = ACK, "ACK"
        else:
            continue

        reply = build_reply(request, message_type=reply_type, offered_ip=offered_ip,
                            server_ip=args.server, subnet_mask=args.subnet_mask,
                            lease_seconds=args.lease_seconds, router=args.router, dns=dns)
        socket_.sendto(reply, (args.broadcast_to, args.reply_port))
        print(f"dhcp: {label} {offered_ip} to {mac.hex()} "
              f"({'discover' if reply_type == OFFER else 'request'})", flush=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ndhcp: stopped", flush=True)
        sys.exit(0)
