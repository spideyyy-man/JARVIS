# JARVIS Connector (browser extension)

Lets JARVIS on this computer read the page you are on and fill forms in your own Chrome or Edge - only after you say yes.

## Install (once)

1. Open `edge://extensions` (Edge) or `chrome://extensions` (Chrome).
2. Turn on **Developer mode**.
3. **Load unpacked** → choose this `extension` folder.
4. JARVIS asks you to approve this browser (the JARVIS icon turns amber; also under Approvals in the JARVIS window).
   Approve it once - the extension then connects by itself. Its icon shows "Connected to JARVIS".
   (Advanced: you can paste the key from JARVIS > Settings > Browser connector instead.)

## Use

- "Jarvis, fill this form" (or "இந்த ஃபார்ம் ஃபில் பண்ணு") on any form: JARVIS reads it, lists what it will put in each
  field from your saved details, and fills them when you say "yes". You press Submit yourself.
- Tell it your details once: "my email is ...", "my college is ...", or JARVIS > Settings > My details.

## What it will never do

- Fill passwords, OTPs, card numbers/CVV/expiry, bank account/IFSC/UPI PIN, Aadhaar, PAN or passport numbers.
- Press Submit on its own (a click on Submit is a separate action that JARVIS always asks about).
- Work on any tab except the one you are looking at, or talk to anything except JARVIS on `127.0.0.1`.
