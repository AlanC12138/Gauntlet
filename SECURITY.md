# Security

## Scope

Gauntlet is a portfolio and interview project, not a deployed service. There is
no production instance, no hosted URL, and no user data — so there is no live
system to attack. What follows is about the code in this repository.

**It is not financial software.** It proposes and verifies allocations over
historical data; it never connects to a brokerage, holds funds, or places an
order. Nothing it outputs is investment advice. See the README's *Known
limitations* for what the numbers do and do not mean.

## Reporting a vulnerability

Open a [private security advisory](https://github.com/AlanC12138/Gauntlet/security/advisories/new),
or a public issue if it is minor and not exploitable. I will respond when I can
— this is a personal project, so please do not expect a commercial SLA.

Useful things to include: the commit you looked at, what an attacker would gain,
and a reproduction if you have one.

## Known and accepted

These are documented in the README rather than hidden, and are deliberate given
the scope above:

- **No per-user data isolation.** `runs` has no owner column, so every
  authenticated user can read every run — including its prompts and costs.
  This would have to change before the project held anyone else's data.
- **No rate limiting on run creation.** Each run costs real money in Anthropic
  API calls. This is the single thing to fix before any public deployment; see
  [DEPLOYING.md](DEPLOYING.md), which leads with it.
- **Default credentials in `docker-compose.yml`.** `gauntlet:gauntlet` is for a
  local development database bound to localhost. It is not a deployment config.

## Secrets

No credential has ever been committed: `.env` and `frontend/.env.local` are
gitignored, and the history contains only placeholder strings such as
`sk_test_...` in `.env.example`. If you believe you have found a real key in
this repository, please report it privately rather than opening an issue.
