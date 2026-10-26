### New

- On a restreamed match in a room the racetime bot runs, the seed now waits for the broadcaster. Once the match's approved broadcaster types `!ready` in the room, the seed goes out (at its usual time, or straight away if that's passed). If nobody has typed `!ready` by the start time, the bot says so in the room and DMs the broadcaster and on-duty staff, and staff can still send the seed with `!forceroll`. This only applies on events with Broadcasters turned on.
