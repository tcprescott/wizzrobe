### Fixed

- When Challonge's monthly API limit runs out, linking your Challonge account now says to try again in a few hours instead of a bare "please try again", and Wizzrobe stops calling Challonge for a while rather than burning more requests.

### Improved

- Bracket syncs from Challonge use fewer API requests: entrants Wizzrobe already knows no longer need a second lookup.
