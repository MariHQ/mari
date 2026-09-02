# Fact validation

Mari validates proposed GitHub changes against facts that people have already
verified in the workspace. It is an evidence check, not a code-quality review.

## When it runs

A pull request is eligible when either:

- a repository owner, member, or collaborator mentions the configured Mari bot
  in a pull-request comment or body; or
- the configured fact-check label is added to the pull request.

Bot-authored events are ignored. Webhook deliveries are recorded durably and a
delivery marker on the resulting comment makes retries safe without posting the
same report twice.

## What Mari checks

Mari fetches the current pull-request title, description, and up to 100 changed
file patches from GitHub. It compares that text with up to 50 verified workspace
facts. Unverified fact candidates are not used for validation.

Each fact receives one of three evidence-based verdicts:

- `supported`: the proposed change agrees with the fact;
- `contradicted`: the proposed change conflicts with the fact; or
- `uncertain`: the pull request does not address the fact, or its evidence
  cannot be verified.

Supported and contradicted verdicts must cite an exact quote that exists in the
pull-request text. A missing, unknown, or fabricated citation is downgraded to
`uncertain`. The pull-request text is treated as untrusted evidence and cannot
instruct the fact checker.

## Result

Mari posts a summary comment with the number of supported, contradicted, and
unaddressed facts, followed by the contradictions and their explanations. If
there are no verified facts or GitHub exposes no readable pull-request text,
the comment says why validation could not run.
