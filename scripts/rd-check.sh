#!/bin/bash
# Which Real-Debrid hostnames are reachable from here.
#
# They are all CNAMEs of the same server on the same IPs, so a split result means
# the network is filtering on the hostname in the TLS handshake — not that
# Real-Debrid is down or your account is limited.
for h in api.real-debrid.com api-1.real-debrid.com api-2.real-debrid.com api-6.real-debrid.com real-debrid.com; do
  code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 12 "https://$h/" 2>/dev/null)
  printf "  %-24s http %s\n" "$h" "${code:-000}"
done
