"""Shared primitives: the typed Intent contract the Arena accepts.

``intents.py`` in this package is a **mirror**, not a source. The Arena service
defines what it will accept, so its copy is authoritative and this one is kept
byte-identical to it — a check in the Arena's CI fails if the two drift.

That is also why nothing has been added to the file itself, not even a comment
saying so: keeping it identical means verifying it is a plain ``diff`` rather
than a judgement call about which differences are cosmetic.

Change the contract in the Arena first, then sync it here.
"""
