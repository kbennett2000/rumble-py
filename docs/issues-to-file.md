# Issues to file

Placeholder notes for issues to file in the GitHub tracker once the repo has
active issue-tracking activity. Keeping them in-tree means they aren't lost
between now and then.

- Add CSRF tokens to POST endpoints before exposing web UI to non-loopback
  addresses. Currently `web.host: 0.0.0.0` is safe only on fully-trusted LANs.
