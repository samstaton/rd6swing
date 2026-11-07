#!/bin/sh
# Serve the loader at http://127.0.0.1:8771/ (a local address counts as secure, so Web MIDI works).
# Add ?sim=1 to the address to try it against a simulated RD-6.
cd "$(dirname "$0")" && exec python3 -m http.server 8771 --bind 127.0.0.1
