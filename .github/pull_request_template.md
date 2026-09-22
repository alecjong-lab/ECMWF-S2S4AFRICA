## What changed and why

One or two sentences. If this changes what subscribers receive(a plot, the
digest wording, the deck layout, the email body), mention explicitly.

## Staging evidence

Before a PR get merged into `main` it needs a `test_pipeline.yml` run on the dev branch, for a recent
date. Pick the branch in the Actions "Run workflow" dropdown, the workflow checks out whatever you select, unlike the production workflows,which are pinned to `main`.

- Run URL:
- Date used (`date_str`):
- Countries:
- Stages left on:

**Check if the output looks right**  Not just "the run was green", workflows have steps use
continue-on-error, so a green run can still have produced nothing.

## Propagation

The four pipeline workflows are meant to stay structurally similar. If this PR
adds or changes a *step* (not just script internals), tick what it needs:

- [ ] `test_pipeline.yml` (staging -- where it should be tried first)
- [ ] `daily_download2.0.yml` (production, scheduled)
- [ ] `daily_download2.0_kenya_only.yml` (production, manual Kenya rerun -- emails subscribers)
- [ ] `specific_download.yml` (production backfill for one date -- no email)
- [ ] Not a workflow step change

If a step is going into a production workflow for the first time, confirm it has
already been exercised in `test_pipeline.yml`.

## Timing

cron job is scheduled to start at 3:33 AM UTC +0, so all changes that will me merged must be tested before this time.