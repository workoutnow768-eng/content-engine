# Order fulfilment — the no-thinking checklist

Run this top to bottom for every content order. Nothing here requires
decisions; decisions were made when the client picked a plan and niche.

## 1. Onboard (5 min)

```
python engine/new_client.py --name acme_cafe --handle @acmecafe \
    --niche food_facts --plan current --accent "#E63946"
```

- `--accent` only if the client gave a brand colour. Omit to keep the pack's own.
- Custom niche request? Copy the closest niche JSON, rename, rewrite entries,
  run `python engine/validate.py` until clean. THEN onboard.

## 2. Sanity gate

- [ ] `python engine/validate.py` passes (run it after ANY pack edit).
- [ ] Client folder exists with config.json + state.json, status `onboarding`.
- [ ] Their platforms are connected in Buffer OR they chose ready-to-post delivery.

## 3. Render the month (2 min)

```
python engine/fulfil.py --client acme_cafe
```

Renders the plan's post count (spark 15 / current 30 / grid 60), starting
after `state.last_index`, wrapping around the bank. Output lands in
`out/acme_cafe/batch_<date>/` with a MANIFEST.txt listing every post +
caption. State is updated automatically.

## 4. Human review (the part that keeps clients)

- [ ] Open every slide. Check: no clipped text, readable contrast, correct handle.
- [ ] Read every caption once. Fix anything that reads wrong, rerender that entry.
- [ ] Spot-check 3 facts you have not verified before. If one is shaky, replace it
      in the pack, validate, rerender.

## 5. Schedule (Buffer flow, same rules as recipe-page)

- [ ] Verify the Buffer sidebar shows THIS client's channels before anything.
- [ ] Remove any default-selected channels that are not theirs.
- [ ] One post per day, client's preferred time (default 18:00 their timezone).
- [ ] Instagram: check the 5-hashtag cap via "Customize for each network".
- [ ] Schedule, then eyeball the queue calendar for gaps or doubles.

## 6. Close out

- [ ] state.json: `status: "live"`, `scheduled_up_to: <last post date>`.
- [ ] Message the client: delivered, first post date, invite feedback.
- [ ] Calendar reminder 5 days before `scheduled_up_to`: renewal message + next batch.

## Renewal (repeat business is the whole model)

Five days before the queue runs dry, message: "Your next month is ready to
go, same niche or want to tweak anything?" Render the next batch the moment
they confirm. Never let a paying client's feed go silent.
